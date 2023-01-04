# -*- coding: utf-8 -*-
from odoo import api, fields, models, _
from odoo.exceptions import ValidationError
from odoo.modules.module import get_module_resource
from jinja2 import Environment, PackageLoader
from lxml import etree, objectify
from datetime import datetime
import base64
import pytz
env = Environment(
    loader=PackageLoader('odoo.addons.trilab_jpk_fa', 'templates'),
    autoescape=True,
    trim_blocks=True,
    lstrip_blocks=True,
)


class JpkFa3(models.TransientModel):
    _name = 'jpk_fa'
    _description = 'JPK FA Wizard'

    state = fields.Selection([('choose', 'choose'), ('get', 'get')], default='choose')
    invoice_ids = fields.Many2many('account.move')
    invoice_len = fields.Integer(compute='compute_invoice_len', store=False)
    invoice_line_ids = fields.Many2many('account.move.line', compute='compute_invoice_line_ids')
    invoice_line_len = fields.Integer(compute='compute_invoice_line_len', store=False)
    jpk_file = fields.Binary()
    jpk_filename = fields.Char()
    company_id = fields.Many2one('res.company')

    @api.model
    def default_get(self, fields_list):
        defaults = super(JpkFa3, self).default_get(fields_list)
        defaults['invoice_ids'] = self.env.context.get('active_ids', False)
        defaults['company_id'] = self.env.user.company_id.id
        return defaults

    def generate_jpk(self):
        if not self.company_id.pl_tax_office_id:
            raise ValidationError(_('Please set tax office for current company "%s"') % self.company_id.name)
        if not self.company_id.state_id:
            raise ValidationError(_('Please set state for current company "%s"') % self.company_id.name)
        if self.invoice_ids.filtered(lambda invoice: invoice.state != 'posted'):
            raise ValidationError(_('Wrong invoice state - Only accepted invoices allowed'))
        if self.invoice_ids.filtered(lambda invoice: invoice.move_type not in ['out_invoice', 'out_refund']):
            raise ValidationError(_('Wrong invoice type - Only sale invoices/corrections allowed'))
        no_tax_lines = self.invoice_ids.mapped('invoice_line_ids').\
            filtered(lambda line: not line.display_type and not line.tax_ids)
        multi_tax_lines = self.invoice_ids.mapped('invoice_line_ids').\
            filtered(lambda line: not line.display_type and len(line.tax_ids) > 1)
        if no_tax_lines:
            raise ValidationError(_('Invoices containing lines without tax: %s') %
                  ', '.join(invoice.name for invoice in no_tax_lines.mapped('move_id')))
        if multi_tax_lines:
            raise ValidationError(_('Invoices containing lines with multiple tax: %s') %
                  ', '.join(invoice.name for invoice in multi_tax_lines.mapped('move_id')))

        xml = env.get_template('jpk_fa_template.xml').render(wizard=self)

        try:
            xsd_path = get_module_resource('trilab_jpk_fa', 'templates', 'jpk_fa.xsd')
            schema = etree.XMLSchema(file=xsd_path)
            parser = objectify.makeparser(schema=schema)
            objectify.fromstring(xml, parser)
        except Exception as exception:
            raise ValidationError(exception)

        self.write({'state': 'get',
                    'jpk_file': base64.b64encode(xml.encode('UTF-8')),
                    'jpk_filename': 'jpk_fa_{}.xml'.format(self.get_create_date())})

        return {
            'type': 'ir.actions.act_window',
            'res_model': 'jpk_fa',
            'view_mode': 'form',
            'view_type': 'form',
            'res_id': self.id,
            'target': 'new',
        }

    def compute_invoice_len(self):
        self.invoice_len = len(self.invoice_ids)

    def compute_invoice_line_ids(self):
        self.invoice_line_ids = self.invoice_ids.mapped('invoice_line_ids').filtered(lambda line: not line.display_type)

    def compute_invoice_line_len(self):
        self.invoice_line_len = len(self.invoice_line_ids)

    def get_create_date(self):
        return datetime.now(pytz.timezone('Europe/Warsaw')).isoformat().split('.')[0]

    def get_date_from(self):
        return min(invoice.invoice_date for invoice in self.invoice_ids)

    def get_date_to(self):
        return max(invoice.invoice_date for invoice in self.invoice_ids)

    def get_invoice_total_value(self):
        return '{:.2f}'.format(sum(invoice.x_amount_total_jpk for invoice in self.invoice_ids))

    def get_invoice_line_total_value(self):
        return '{:.2f}'.format(sum(line.x_price_subtotal_jpk for line in self.invoice_ids.mapped('invoice_line_ids')))
