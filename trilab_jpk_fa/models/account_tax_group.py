# -*- coding: utf-8 -*-
from odoo import models, fields


class AccountTaxGroup(models.Model):

    _inherit = 'account.tax.group'

    x_amount_int = fields.Integer(compute='x_compute_x_amount_int', store=False)

    def x_compute_x_amount_int(self):
        for group in self:
            tax_amount = self.env['account.tax'].search([('tax_group_id', '=', group.id)]).mapped('amount')
            tax_amount = list(set(tax_amount))
            if len(tax_amount) == 1:
                group.x_amount_int = int(tax_amount[0])
            else:
                group.x_amount_int = -1
