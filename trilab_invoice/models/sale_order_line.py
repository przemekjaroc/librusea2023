# -*- coding: utf-8 -*-

from odoo import models


class SaleOrderLine(models.Model):
    _inherit = 'sale.order.line'

    def _x_prepare_invoice_line(self, line_list=False, **optional_values):
        self.ensure_one()
        quantity = self.qty_to_invoice
        if self.is_downpayment and line_list and quantity < 0:
            sum_field = 'price_total' if self.tax_id.price_include else 'price_subtotal'
            invoice_lines = line_list.filtered(lambda l: not l.is_downpayment and l.tax_id.ids == self.tax_id.ids)
            so_lines = self.order_id.order_line.filtered(
                lambda l: not l.is_downpayment and l.tax_id.ids == self.tax_id.ids)
            invoice_value = sum(
                line.qty_to_invoice * (line[sum_field] / line.product_uom_qty) for line in invoice_lines)
            so_value = sum(line[sum_field] for line in so_lines)
            quantity = -1 * (invoice_value / so_value)
        res = self._prepare_invoice_line(sequence=optional_values['sequence'])
        res['quantity'] = quantity
        return res
