import json
import logging
from collections import defaultdict
from datetime import timedelta

# noinspection PyProtectedMember
from odoo import api, fields, models, _
from odoo.exceptions import UserError, ValidationError, AccessError
from odoo.tools import get_lang, float_compare, format_date, formatLang

_logger = logging.getLogger(__name__)


# noinspection DuplicatedCode,PyProtectedMember
class AccountMove(models.Model):
    _inherit = 'account.move'

    @api.depends('refund_invoice_id')
    def compute_original_invoice_line_ids(self):
        for invoice in self:
            if invoice.move_type not in ['in_refund', 'out_refund'] or not invoice.refund_invoice_id:
                invoice.original_invoice_line_ids = False
                return
            invoice.original_invoice_line_ids = invoice.invoice_line_ids \
                .filtered(lambda l: not l.exclude_from_invoice_tab and not l.corrected_line).ids

    @api.constrains('refund_invoice_id', 'selected_correction_invoice')
    def check_correction_invoice(self):
        for invoice in self:
            if invoice.refund_invoice_id and not invoice.selected_correction_invoice:
                if self.search([('refund_invoice_id', '=', self.refund_invoice_id.id),
                                ('selected_correction_invoice', '=', False)], count=True) > 1:
                    raise ValidationError(_('It is not possible to issue two direct corrections for one invoice.'))
            if invoice.refund_invoice_id and invoice.selected_correction_invoice:
                if self.search([('refund_invoice_id', '=', self.refund_invoice_id.id),
                                ('selected_correction_invoice', '=', invoice.selected_correction_invoice.id)],
                               count=True) > 1:
                    raise ValidationError(_('It is not possible to issue two direct corrections for one correction.'))

    @api.constrains('state')
    def clock_moving_back(self):
        for invoice in self:
            if invoice.state not in ['draft', 'cancel']:
                continue
            if invoice.correction_invoices_len:
                raise ValidationError(_('An invoice cannot be modified if it is associated with corrections.\n'
                                        'Delete corrections or create a new correction to an existing correction'))

    def get_connected_corrections(self):
        selected_invoice = self
        corrections = self.env['account.move']
        while True:
            correction = self.search([('selected_correction_invoice', '=', selected_invoice.id)])
            if not correction:
                break
            selected_invoice = correction
            corrections += selected_invoice
        return corrections

    def compute_correction_invoices_len(self):
        for invoice in self:
            if invoice.move_type in ['in_invoice', 'out_invoice']:
                invoice.correction_invoices_len = len(invoice.correction_invoices_ids)
            else:
                corrections = invoice.get_connected_corrections()
                invoice.correction_invoices_len = len(corrections)

    def _compute_corrected_invoice_line_ids(self):
        for invoice in self:
            invoice.corrected_invoice_line_ids = self.env['account.move.line'] \
                .search([('move_id', '=', invoice.id),
                         ('exclude_from_invoice_tab', '=', False),
                         ('corrected_line', '=', True)])

    correction_invoices_ids = fields.One2many('account.move', 'refund_invoice_id')
    correction_invoices_len = fields.Integer(compute=compute_correction_invoices_len, store=False)
    refund_invoice_id = fields.Many2one('account.move')

    original_invoice_line_ids = fields.Many2many(comodel_name='account.move.line',
                                                 string='Original Invoice Lines',
                                                 compute=compute_original_invoice_line_ids,
                                                 readonly=True, store=False, tracking=False)
    corrected_invoice_line_ids = fields.One2many('account.move.line', 'move_id', string='Corrected Invoice lines',
                                                 readonly=True, compute='_compute_corrected_invoice_line_ids',
                                                 states={'draft': [('readonly', False)]})

    selected_correction_invoice = fields.Many2one('account.move')

    x_invoice_sale_date = fields.Date(string='Sale Date', default=fields.Datetime.now)
    x_invoice_duplicate_date = fields.Date(string='Duplicate Date', copy=False)

    # connected sale order (for advance invoice pdf)
    advance_source_id = fields.Many2one('sale.order', compute='compute_advance_source_id', store=False)
    # connected final invoice (for advance invoice pdf)
    final_invoice_ids = fields.Many2many('account.move', compute='compute_advance_source_id', store=False)
    # connected sale order (for final invoice pdf)
    final_source_id = fields.Many2one('sale.order', compute='compute_advance_invoices_ids', store=False)
    # connected advance invoices (for final invoice pdf)
    advance_invoices_ids = fields.Many2many('account.move', compute='compute_advance_invoices_ids', store=False)

    is_downpayment = fields.Boolean()
    x_is_poland = fields.Boolean(compute='_x_compute_is_poland', string='Technical Field: Is Poland')
    x_invoice_sign = fields.Integer(compute='x_compute_invoice_sign')
    x_corrected_amount_total = fields.Float(compute='_x_compute_corrected_amount_total')

    def _x_compute_is_poland(self):
        for rec in self:
            rec.x_is_poland = rec.env.company.country_id.id == rec.env.ref('base.pl').id

    def get_final_invoice_summary(self):
        deposit_product_id = int(self.env['ir.config_parameter'].sudo().get_param('sale.default_deposit_product_id'))
        lines = self.invoice_line_ids.filtered(lambda l: l.quantity >= 0 or l.product_id.id == deposit_product_id)
        taxes = lines.mapped('tax_ids').mapped('tax_group_id')
        output_list = list()
        for tax in taxes:
            tax_lines = lines.filtered(lambda l: tax.id in l.tax_ids.mapped('tax_group_id').ids)
            netto = sum(line.price_subtotal for line in tax_lines)
            brutto = sum(line.price_total for line in tax_lines)
            output_list.append(dict(
                tax=tax.name,
                netto=netto,
                brutto=brutto,
                tax_value=brutto - netto,
                in_pln=self.currency_id._convert(brutto - netto, self.company_currency_id, self.company_id, self.date),
            ))
        output = dict(
            tax_list=output_list,
            netto=sum(tax['netto'] for tax in output_list),
            brutto=sum(tax['brutto'] for tax in output_list),
            tax_value=sum(tax['tax_value'] for tax in output_list),
            in_pln=sum(tax['in_pln'] for tax in output_list),
        )
        return output

    def _prepare_tax_lines_data_for_totals_from_invoice(self, tax_line_id_filter=None, tax_ids_filter=None):
        result = super(AccountMove, self)._prepare_tax_lines_data_for_totals_from_invoice(tax_line_id_filter,
                                                                                          tax_ids_filter)

        if not self.x_is_poland:
            return result

        tax_line_id_filter = tax_line_id_filter or (lambda aml, tax: True)
        tax_ids_filter = tax_ids_filter or (lambda aml, tax: True)

        balance_multiplicator = -1 if self.is_inbound() else 1
        tax_lines_data = []

        for line in self.line_ids:
            if line.tax_line_id and tax_line_id_filter(line, line.tax_line_id):
                tax_lines_data.append({
                    'line_key': 'tax_line_%s' % line.id,
                    'tax_amount': line.amount_currency * balance_multiplicator,
                    'tax': line.tax_line_id,
                    'x_balance': line.balance * balance_multiplicator,
                    'x_invoice_sign': line.move_id.x_invoice_sign,
                })

            if line.tax_ids:
                for base_tax in line.tax_ids.flatten_taxes_hierarchy():
                    if tax_ids_filter(line, base_tax):
                        tax_lines_data.append({
                            'line_key': 'base_line_%s' % line.id,
                            'base_amount': line.amount_currency * balance_multiplicator,
                            'tax': base_tax,
                            'tax_affecting_base': line.tax_line_id,
                            'x_balance': line.balance * balance_multiplicator,
                            'x_invoice_sign': line.move_id.x_invoice_sign,
                        })

        return tax_lines_data

    @api.model
    def _get_tax_totals(self, partner, tax_lines_data, amount_total, amount_untaxed, currency):
        result = super(AccountMove, self)._get_tax_totals(partner, tax_lines_data, amount_total, amount_untaxed,
                                                          currency)
        if self.env.company.country_id.id != self.env.ref('base.pl').id:
            return result

        lang_env = self.with_context(lang=partner.lang).env
        account_tax = self.env['account.tax']
        pln = self.env.company.currency_id

        tax_amount_in_pln = 0

        grouped_taxes = defaultdict(lambda: defaultdict(lambda: {'base_amount': 0.0,
                                                                 'tax_amount': 0.0,
                                                                 'x_balance_amount': 0.0,
                                                                 'base_line_keys': set()}))
        subtotal_priorities = {}
        x_invoice_sign = 1
        for line_data in tax_lines_data:
            tax_group = line_data['tax'].tax_group_id
            x_invoice_sign = line_data.get('x_invoice_sign', 1)

            # Update subtotals priorities
            if tax_group.preceding_subtotal:
                subtotal_title = tax_group.preceding_subtotal
                new_priority = tax_group.sequence
            else:
                # When needed, the default subtotal is always the most prioritary
                subtotal_title = _("Untaxed Amount")
                new_priority = 0

            if subtotal_title not in subtotal_priorities or new_priority < subtotal_priorities[subtotal_title]:
                subtotal_priorities[subtotal_title] = new_priority

            # Update tax data
            tax_group_vals = grouped_taxes[subtotal_title][tax_group]

            if 'base_amount' in line_data:
                # Base line
                if tax_group == line_data.get('tax_affecting_base', account_tax).tax_group_id:
                    # In case the base has a tax_line_id belonging to the same group as the base tax, the base for
                    # the group will be computed by the base tax's original line (the one with tax_ids and no
                    # tax_line_id)
                    continue

                if line_data['line_key'] not in tax_group_vals['base_line_keys']:
                    # If the base line hasn't been taken into account yet, at its amount to the base total.
                    tax_group_vals['base_line_keys'].add(line_data['line_key'])
                    tax_group_vals['base_amount'] += line_data['base_amount']

            else:
                # Tax line

                balance = line_data.get('x_balance', 0.0)
                tax_group_vals['tax_amount'] += line_data['tax_amount']
                tax_group_vals['x_balance_amount'] += balance
                tax_amount_in_pln += balance

        for groups in grouped_taxes.values():
            for amounts in groups.values():
                for key in ('base_amount', 'tax_amount', 'x_balance_amount'):
                    amounts[key] = x_invoice_sign * abs(amounts.get(key, 0))
                    print(f'{amounts[key]=}')


        # Compute groups_by_subtotal
        groups_by_subtotal = {}
        for subtotal_title, groups in grouped_taxes.items():
            groups_vals = [{
                'tax_group_name': group.name,
                'tax_group_amount': amounts['tax_amount'],
                'tax_group_base_amount': amounts['base_amount'],
                'x_tax_group_total_amount': amounts['tax_amount'] + amounts['base_amount'],
                'x_tax_group_amount_in_pln': amounts['x_balance_amount'],
                'formatted_tax_group_amount': formatLang(lang_env, amounts['tax_amount'], currency_obj=currency),
                'formatted_tax_group_base_amount': formatLang(lang_env, amounts['base_amount'], currency_obj=currency),
                'x_formatted_tax_group_amount_in_pln': formatLang(lang_env, amounts['x_balance_amount'],
                                                                  currency_obj=pln),
                'x_formatted_tax_group_total_amount': formatLang(lang_env,
                                                                 amounts['tax_amount'] + amounts['base_amount'],
                                                                 currency_obj=currency),
                'tax_group_id': group.id,
                'group_key': '%s-%s' % (subtotal_title, group.id),
            } for group, amounts in sorted(groups.items(), key=lambda l: l[0].sequence)]

            groups_by_subtotal[subtotal_title] = groups_vals

        # Compute subtotals
        subtotals_list = []  # List, so that we preserve their order
        previous_subtotals_tax_amount = 0
        for subtotal_title in sorted((sub for sub in subtotal_priorities), key=lambda x: subtotal_priorities[x]):
            subtotal_value = amount_untaxed + previous_subtotals_tax_amount
            subtotals_list.append({
                'name': subtotal_title,
                'amount': subtotal_value,
                'formatted_amount': formatLang(lang_env, subtotal_value, currency_obj=currency),
            })

            subtotal_tax_amount = sum(group_val['tax_group_amount'] for group_val in groups_by_subtotal[subtotal_title])
            previous_subtotals_tax_amount += subtotal_tax_amount

        amount_total = x_invoice_sign * abs(amount_total)
        amount_untaxed = x_invoice_sign * abs(amount_untaxed)
        tax_amount = amount_total - amount_untaxed

        # Assign json-formatted result to the field
        return {
            'amount_total': amount_total,
            'amount_untaxed': amount_untaxed,
            'formatted_amount_total': formatLang(lang_env, amount_total, currency_obj=currency),
            'formatted_amount_untaxed': formatLang(lang_env, amount_untaxed, currency_obj=currency),
            'groups_by_subtotal': groups_by_subtotal,
            'subtotals': subtotals_list,
            'allow_tax_edition': False,

            'x_tax_amount': tax_amount,
            'x_formatted_tax_amount': formatLang(lang_env, tax_amount, currency_obj=currency),
            'x_tax_amount_in_pln': tax_amount_in_pln,
            'x_formatted_tax_amount_in_pln': formatLang(lang_env, tax_amount_in_pln, currency_obj=pln)
        }

    def compute_advance_source_id(self):
        for invoice in self:
            advance_lines = self.env['sale.order.line'].search([
                ('is_downpayment', '=', True),
                ('invoice_lines.id', 'in', invoice.invoice_line_ids.filtered(lambda line: line.credit > 0).ids)
            ])
            invoice.advance_source_id = advance_lines.mapped('order_id').id
            invoice.final_invoice_ids = advance_lines.mapped('invoice_lines').\
                filtered(lambda line: line.debit > 0).mapped('move_id').ids

    def compute_advance_invoices_ids(self):
        for invoice in self:
            final_lines = self.env['sale.order.line'].search([
                ('is_downpayment', '=', True),
                ('invoice_lines.id', 'in', invoice.invoice_line_ids.filtered(lambda line: line.debit > 0).ids)
            ])
            invoice.advance_invoices_ids = final_lines.\
                mapped('invoice_lines').filtered(lambda line: line.credit > 0).mapped('move_id').ids
            invoice.final_source_id = final_lines.mapped('order_id').id

    @api.model
    def _move_autocomplete_invoice_lines_create(self, vals_list):
        result = super(AccountMove, self)._move_autocomplete_invoice_lines_create(vals_list)
        if self.env.company.country_id.id != self.env.ref('base.pl').id:
            return result
        for vals in result:
            if vals.get('move_type') not in ['in_refund', 'out_refund'] and 'corrected_invoice_line_ids' in vals:
                del(vals['corrected_invoice_line_ids'])
        return result

    @api.model_create_multi
    def create(self, vals_list):
        if self.env.company.country_id.id != self.env.ref('base.pl').id:
            return super(AccountMove, self).create(vals_list)

        if self.env.context.get('x_journal_id', False):
            for vals in vals_list:
                vals['journal_id'] = self.env.context['x_journal_id']
        invoice_ids = super(AccountMove, self).create(vals_list)
        for invoice, vals in zip(invoice_ids, vals_list):
            if invoice.move_type in ('in_refund', 'out_refund'):
                invoice.refund_invoice_id = vals.get('reversed_entry_id')

                if invoice.selected_correction_invoice:
                    # correction to the correction
                    invoice.invoice_line_ids.with_context(check_move_validity=False).unlink()
                    for line in invoice.selected_correction_invoice.corrected_invoice_line_ids:
                        copied_vals = line.with_context(include_business_fields=True, check_move_validity=False)\
                            .copy_data(default={'move_id': invoice.id,
                                                'price_unit': -line.price_unit,
                                                'corrected_line': False})[0]
                        copied = self.env['account.move.line'].create(copied_vals)
                        # copied.price_unit = -line.price_unit
                        copied.quantity = -line.quantity
                        copied.run_onchanges()

                    for line in invoice.selected_correction_invoice.corrected_invoice_line_ids:
                        copied_vals = line.with_context(include_business_fields=True, check_move_validity=False)\
                            .copy_data(default={'move_id': invoice.id,
                                                'price_unit': line.price_unit,
                                                'corrected_line': True})[0]
                        copied = self.env['account.move.line'].create(copied_vals)
                        copied.quantity = -abs(line.quantity)
                        copied.run_onchanges()

                else:
                    for line in invoice.invoice_line_ids:
                        copied_vals = line.with_context(include_business_fields=True, check_move_validity=False)\
                            .copy_data(default={'move_id': invoice.id,
                                                'corrected_line': True})[0]
                        copied = self.env['account.move.line'].create(copied_vals)
                        copied.quantity = -line.quantity
                        copied.run_onchanges()

                invoice.with_context(check_move_validity=False)._onchange_invoice_line_ids()
                invoice._compute_tax_totals_json()

        return invoice_ids

    @api.constrains('corrected_invoice_line_ids', 'move_type')
    def constrains_correction_data(self):
        for invoice in self:
            if invoice.move_type in ['in_refund', 'out_refund'] and invoice.corrected_invoice_line_ids:

                for line in invoice.invoice_line_ids:
                    line.run_onchanges()

                invoice._onchange_invoice_line_ids()
                invoice._recompute_dynamic_lines(True)
                invoice._compute_tax_totals_json()

    def correction_invoices_view(self):
        view_data = {
            'name': _("Correction Invoices"),
            'view_mode': 'tree,form',
            'res_model': 'account.move',
            'type': 'ir.actions.act_window'
        }

        if self.move_type in ['in_invoice', 'out_invoice']:
            view_data['domain'] = [('id', 'in', self.correction_invoices_ids.ids)]

        else:
            view_data['domain'] = [('id', 'in', self.get_connected_corrections().ids)]

        return view_data

    def action_reverse(self):
        if not self.x_is_poland:
            return super(AccountMove, self).action_reverse()

        ctx = dict(self.env.context)
        rec = self

        if self.refund_invoice_id:
            ctx['active_id'] = self.refund_invoice_id.id
            ctx['active_ids'] = [self.refund_invoice_id.id]
            rec = self.refund_invoice_id
        rec = rec.with_context(ctx)
        action = rec.env.ref('account.action_view_account_move_reversal').read()[0]
        if rec.is_invoice():
            action['name'] = _('Credit Note')

        return action

    # changes in existing methods

    def action_post(self):
        if not any(self.mapped('x_is_poland')):
            return super(AccountMove, self).action_post()

        for invoice in self:
            if invoice.move_type in ('in_invoice', 'in_receipt', 'in_refund') and not invoice.ref:
                raise ValidationError(_('Vendor invoice number is required'))
        return super(AccountMove, self).action_post()

    def _post(self, soft=True):
        if not any(self.mapped('x_is_poland')):
            return super(AccountMove, self)._post()

        for move in self:
            if move.move_type in ['out_refund', 'in_refund'] and\
                    float_compare(move.amount_total, 0.0, precision_rounding=move.currency_id.rounding) < 0:
                return self.post_alternative_method(soft)
        return super(AccountMove, self)._post()

    def post_alternative_method(self, soft=True):
        if soft:
            future_moves = self.filtered(lambda move: move.date > fields.Date.context_today(self))
            future_moves.auto_post = True
            for move in future_moves:
                msg = _('This move will be posted at the accounting date: %(date)s',
                        date=format_date(self.env, move.date))
                move.message_post(body=msg)
            to_post = self - future_moves
        else:
            to_post = self
        if not self.env.is_superuser() and not self.env.user.has_group('account.group_account_invoice'):
            raise AccessError(_("You don't have the access rights to post an invoice."))
        for move in to_post:
            if not move.line_ids.filtered(lambda line: not line.display_type):
                raise UserError(_('You need to add a line before posting.'))
            if move.auto_post and move.date > fields.Date.context_today(self):
                date_msg = move.date.strftime(get_lang(self.env).date_format)
                raise UserError(_("This move is configured to be auto-posted on %s", date_msg))
            if not move.partner_id:
                if move.is_sale_document():
                    raise UserError(_("The field 'Customer' is required, "
                                      "please complete it to validate the Customer Invoice."))
                elif move.is_purchase_document():
                    raise UserError(_("The field 'Vendor' is required, "
                                      "please complete it to validate the Vendor Bill."))
            # CHANGE TO DEFAULT METHOD
            # if move.is_invoice(include_receipts=True) and float_compare(
            #         move.amount_total, 0.0, precision_rounding=move.currency_id.rounding) < 0:
            #     raise UserError(_("You cannot validate an invoice with a negative total amount. "
            #                       "You should create a credit note instead. "
            #                       "Use the action menu to transform it into a credit note or refund."))
            if not move.invoice_date and move.is_invoice(include_receipts=True):
                move.invoice_date = fields.Date.context_today(self)
                move.with_context(check_move_validity=False)._onchange_invoice_date()
            if (move.company_id.tax_lock_date and move.date <= move.company_id.tax_lock_date) and\
                    (move.line_ids.tax_ids or move.line_ids.tax_tag_ids):
                move.date = move.company_id.tax_lock_date + timedelta(days=1)
                move.with_context(check_move_validity=False)._onchange_currency()
        to_post.mapped('line_ids').create_analytic_lines()
        to_post.write({
            'state': 'posted',
            'posted_before': True,
        })
        for move in to_post:
            move.message_subscribe([p.id for p in [move.partner_id] if p not in move.sudo().message_partner_ids])
            if move._auto_compute_invoice_reference():
                to_write = {
                    'payment_reference': move._get_invoice_computed_reference(),
                    'line_ids': []
                }
                for line in move.line_ids.filtered(
                        lambda l: l.account_id.user_type_id.type in ('receivable', 'payable')):
                    to_write['line_ids'].append((1, line.id, {'name': to_write['payment_reference']}))
                move.write(to_write)
        for move in to_post:
            if move.is_sale_document()\
                    and move.journal_id.sale_activity_type_id\
                    and (move.journal_id.sale_activity_user_id or
                         move.invoice_user_id).id not in (self.env.ref('base.user_root').id, False):
                move.activity_schedule(
                    date_deadline=min((date for date in move.line_ids.mapped('date_maturity') if date),
                                      default=move.date),
                    activity_type_id=move.journal_id.sale_activity_type_id.id,
                    summary=move.journal_id.sale_activity_note,
                    user_id=move.journal_id.sale_activity_user_id.id or move.invoice_user_id.id,
                )
        customer_count, supplier_count = defaultdict(int), defaultdict(int)
        for move in to_post:
            if move.is_sale_document():
                customer_count[move.partner_id] += 1
            elif move.is_purchase_document():
                supplier_count[move.partner_id] += 1
        for partner, count in customer_count.items():
            partner._increase_rank('customer_rank', count)
        for partner, count in supplier_count.items():
            partner._increase_rank('supplier_rank', count)
        to_post.filtered(
            lambda m: m.is_invoice(include_receipts=True) and m.currency_id.is_zero(m.amount_total)
        ).action_invoice_paid()
        to_post._check_balanced()
        return to_post

    def _compute_payments_widget_to_reconcile_info(self):
        if not any(self.mapped('x_is_poland')):
            return super(AccountMove, self)._compute_payments_widget_to_reconcile_info()

        for move in self:
            move.invoice_outstanding_credits_debits_widget = json.dumps(False)
            move.invoice_has_outstanding = False

            if move.state != 'posted' or move.payment_state not in ('not_paid', 'partial')\
                    or not move.is_invoice(include_receipts=True):
                continue
            pay_term_line_ids = move.line_ids.filtered(lambda _line:
                                                       _line.account_id.user_type_id.type in ('receivable', 'payable'))

            domain = [
                ('account_id', 'in', pay_term_line_ids.mapped('account_id').ids),
                '|', ('move_id.state', '=', 'posted'),
                    ('move_id.state', '=', 'draft'),
                ('partner_id', '=', move.commercial_partner_id.id),
                ('reconciled', '=', False),
                '|', ('amount_residual', '!=', 0.0),
                    ('amount_residual_currency', '!=', 0.0)]

            if move.is_inbound():
                if move.amount_total < 0:
                    domain.extend([('credit', '=', 0), ('debit', '>', 0)])
                    type_payment = _('Outstanding debits')
                else:
                    domain.extend([('credit', '>', 0), ('debit', '=', 0)])
                    type_payment = _('Outstanding credits')
            else:
                if move.amount_total < 0:
                    domain.extend([('credit', '>', 0), ('debit', '=', 0)])
                    type_payment = _('Outstanding credits')
                else:
                    domain.extend([('credit', '=', 0), ('debit', '>', 0)])
                    type_payment = _('Outstanding debits')

            info = {'title': '', 'outstanding': True, 'content': [], 'move_id': move.id}
            lines = self.env['account.move.line'].search(domain)
            currency_id = move.currency_id
            if len(lines) != 0:
                for line in lines:
                    # get the outstanding residual value in invoice currency
                    if line.currency_id and line.currency_id == move.currency_id:
                        amount_to_show = abs(line.amount_residual_currency)
                    else:
                        currency = line.company_id.currency_id
                        amount_to_show = currency._convert(abs(line.amount_residual), move.currency_id, move.company_id,
                                                           line.date or fields.Date.today())

                    if move.currency_id.is_zero(amount_to_show):
                        continue

                    info['content'].append({
                        'journal_name': line.ref or line.move_id.name,
                        'amount': amount_to_show,
                        'currency': currency_id.symbol,
                        'id': line.id,
                        'move_id': line.move_id.id,  # ?
                        'position': currency_id.position,
                        'digits': [69, move.currency_id.decimal_places],
                        'payment_date': fields.Date.to_string(line.date),
                    })

                info['title'] = type_payment
                move.invoice_outstanding_credits_debits_widget = json.dumps(info)
                move.invoice_has_outstanding = True

    # noinspection PyMethodMayBeStatic
    def _format_float(self, number, currency, env):
        return formatLang(env, 0.0 if currency.is_zero(number) else number, currency_obj=currency)

    def _reverse_move_vals(self, default_values, cancel=True):
        if not self.x_is_poland:
            return super(AccountMove, self)._reverse_move_vals(default_values, cancel)

        force_type = None
        if 'selected_correction_invoice' in default_values and default_values['selected_correction_invoice']:
            selected_correction_invoice_id = self.browse([default_values['selected_correction_invoice']])
            force_type = selected_correction_invoice_id.move_type

        result = super(AccountMove, self)._reverse_move_vals(default_values, cancel)

        if force_type:
            result['move_type'] = force_type

        return result

    def action_reverse_pl(self):
        action = self.env.ref('trilab_invoice.action_view_account_move_reversal_pl').sudo().read()[0]

        if self.is_invoice():
            action['name'] = _('Credit Note PL')

        return action

    def x_num2words(self, amount, currency):
        amount = '{:.2f}'.format(amount)
        lang = self.env.context.get('lang', 'en')
        # If template preview
        tmpl_id = self._context.get('default_mail_template_id')
        if tmpl_id:
            template = self.env['mail.template'].browse([tmpl_id])
            if template.lang:
                lang = template._render_lang(self.ids)[self.id]
        try:
            from num2words import num2words
            # noinspection PyBroadException
            try:
                return num2words(amount, lang=lang, to='currency', currency=currency)
            except NotImplementedError:
                _logger.warning('num2words - unsupported language')
                return ''
            except Exception:
                # currency convert unsupported for this language (no proper exception returned)
                return num2words(amount, lang=lang)
        except ImportError:
            _logger.warning('num2words not installed, no text2word for invoice')
            return ''

    @api.depends('corrected_invoice_line_ids.quantity', 'corrected_invoice_line_ids.price_unit',
                 'corrected_invoice_line_ids.discount', 'refund_invoice_id')
    def _x_compute_corrected_amount_total(self):
        for move in self:
            move.x_corrected_amount_total = move.amount_total
            if move.refund_invoice_id:
                move.x_corrected_amount_total = sum(abs(line.quantity * line.price_unit) *
                                                    (1 - (line.discount or 0) / 100)
                                                    for line in move.corrected_invoice_line_ids)

    @api.depends('move_type', 'refund_invoice_id.amount_total', 'amount_total', 'x_corrected_amount_total')
    def x_compute_invoice_sign(self):
        for move in self:
            move.x_invoice_sign = 1
            if move.move_type in ('out_refund', 'in_refund') and \
                    not float_compare(move.x_corrected_amount_total,
                                      move.refund_invoice_id.x_corrected_amount_total, 2) > 0:
                move.x_invoice_sign = -1
