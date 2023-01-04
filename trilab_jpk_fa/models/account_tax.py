# -*- coding: utf-8 -*-
from odoo import models


class AccountTax(models.Model):

    _inherit = 'account.tax'

    def x_get_amount_int(self):
        return int(self.amount)
