# -*- coding: utf-8 -*-
from odoo import models


class ResPartner(models.Model):

    _inherit = 'res.partner'

    def x_get_nip(self):
        return ''.join(c for c in self.vat if c.isdigit())

    def x_get_full_address(self):
        address_list = list(filter(None, [self.street, self.street2, self.zip, self.city, self.country_id.name]))
        return ', '.join(address_list)
