from odoo import models, fields, _


class WhitelistPartnerBank(models.TransientModel):
    _name = 'trilab.wl.partner.bank'
    _description = 'Trilab Whitelist Partner Bank Account Wizard'

    wl_wizard_id = fields.Many2one('trilab.wl.wizard')
    acc_number = fields.Char('Account Number')


class WhitelistWizard(models.TransientModel):
    _name = 'trilab.wl.wizard'
    _description = 'Trilab Whitelist Wizard'

    banks_ids = fields.One2many('trilab.wl.partner.bank', 'wl_wizard_id')
    selected_banks_ids = fields.Many2many('trilab.wl.partner.bank', string='Selected Bank Accounts')
    partner_id = fields.Many2one('res.partner')

    def save_selected_banks(self):
        self.ensure_one()

        self.partner_id.write({'bank_ids': [(0, 0, {'acc_number': bank.acc_number,
                                                    'partner_id': self.partner_id}) for bank in
                                            self.selected_banks_ids]})

        if self.selected_banks_ids:
            self.partner_id.message_post(
                body=_('Bank accounts added from Whitelist of Ministry of Finance: %s') % ', '.join(
                    [bank.acc_number for bank in self.selected_banks_ids]))
