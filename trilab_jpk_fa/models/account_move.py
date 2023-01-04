# -*- coding: utf-8 -*-
import json

from odoo import fields, models
from decimal import Decimal, ROUND_HALF_UP


class AccountMove(models.Model):

    _inherit = 'account.move'

    x_amount_untaxed_jpk = fields.Float(compute='x_compute_amount_untaxed_jpk', store=False)
    x_amount_total_jpk = fields.Float(compute='x_compute_amount_total_jpk', store=False)
    x_rate = fields.Float(compute='x_compute_rate', store=False)

    def x_compute_rate(self):
        for invoice in self:
            invoice.x_rate = 1.0 / invoice.currency_id.with_context(
                dict(self._context or {}, date=invoice.invoice_date)).rate

    def x_jpk_get_eu_code(self):
        if self.partner_id.country_id.id in self.env.ref('base.europe').country_ids.ids:
            return self.partner_id.country_id.code
        return ''

    def x_get_jpk_net(self, tax_list):
        tax_groups = self.env['account.tax.group'].search([]).filtered(lambda group: group.x_amount_int in tax_list)
        value_net = 0
        invoice_totals = json.loads(self.tax_totals_json)
        for amount_by_group_list in invoice_totals['groups_by_subtotal'].values():
            for group in amount_by_group_list:
                if group['tax_group_id'] not in tax_groups.ids:
                    continue
                value_net += group['tax_group_base_amount']
        if self.move_type == 'out_refund':
            value_net *= -1
        value_net = float(Decimal(value_net).quantize(Decimal('0.01'), ROUND_HALF_UP))
        return '{:.2f}'.format(value_net)

    def x_get_jpk_tax(self, tax_list, convert=False):
        tax_groups = self.env['account.tax.group'].search([]).filtered(lambda group: group.x_amount_int in tax_list)
        value_tax = 0
        invoice_totals = json.loads(self.tax_totals_json)
        for amount_by_group_list in invoice_totals['groups_by_subtotal'].values():
            for group in amount_by_group_list:
                if group['tax_group_id'] not in tax_groups.ids:
                    continue
                value_tax += group['tax_group_amount']
        if self.move_type == 'out_refund':
            value_tax *= -1
        if convert:
            value_tax *= (self.x_rate or 1)
        value_tax = float(Decimal(value_tax).quantize(Decimal('0.01'), ROUND_HALF_UP))
        return '{:.2f}'.format(value_tax)

    def x_compute_amount_untaxed_jpk(self):
        for invoice in self:
            invoice.x_amount_untaxed_jpk = invoice.amount_untaxed if (invoice.move_type == 'out_invoice') \
                else -invoice.amount_untaxed

    def x_compute_amount_total_jpk(self):
        for invoice in self:
            invoice.x_amount_total_jpk = invoice.amount_total if (invoice.move_type == 'out_invoice') \
                else -invoice.amount_total
