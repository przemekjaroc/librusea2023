# -*- coding: utf-8 -*-
from odoo import fields, models


class AccountMoveLine(models.Model):

    _inherit = 'account.move.line'

    x_price_unit_jpk = fields.Float(compute='x_compute_price_unit_jpk', store=False)
    x_price_subtotal_jpk = fields.Float(compute='x_compute_price_subtotal_jpk', store=False)
    x_price_total_jpk = fields.Float(compute='x_compute_price_total_jpk', store=False)

    def x_compute_price_unit_jpk(self):
        for line in self:
            line.x_price_unit_jpk = line.price_unit if (line.move_id.move_type == 'out_invoice') else -line.price_unit

    def x_compute_price_subtotal_jpk(self):
        for line in self:
            line.x_price_subtotal_jpk = line.price_subtotal if (line.move_id.move_type == 'out_invoice') \
                else -line.price_subtotal

    def x_compute_price_total_jpk(self):
        for line in self:
            line.x_price_total_jpk = line.price_total if (line.move_id.move_type == 'out_invoice') else -line.price_total
