from odoo import models, fields, api


class CurrencyRate(models.Model):
    _inherit = 'res.currency.rate'

    x_rate_inverted = fields.Float(digits=0, string='Rate Inverted', compute='_x_compute_rate_inverted')

    @api.depends('rate')
    def _x_compute_rate_inverted(self):
        for curr_rate in self:
            curr_rate.x_rate_inverted = 1 / curr_rate.rate

    def name_get(self):
        return [(curr_rate.id, f'{curr_rate.currency_id.name} - {curr_rate.name} - {curr_rate.x_rate_inverted}')
                for curr_rate in self]
