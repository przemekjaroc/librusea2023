# noinspection PyProtectedMember
from odoo import api, fields, models, _


class CheckPartnerGusDetail(models.TransientModel):
    _name = 'trilab.check.partner.gus'
    _description = 'Trilab Check GUS Partner'

    partner_id = fields.Many2one('res.partner')
    details_id = fields.Many2one('trilab.check.partner.details')

    name = fields.Char()
    street = fields.Char()
    street2 = fields.Char()
    city = fields.Char()
    state_id = fields.Many2one('res.country.state')
    zip = fields.Char()
    country_id = fields.Many2one('res.country')
    phone = fields.Char()
    email = fields.Char()
    website = fields.Char()
    regon = fields.Char()
    krs = fields.Char()
    x_pl_gus_update_date = fields.Date()
    vat = fields.Char()
    lang = fields.Char()
    x_pl_business_type = fields.Char()


class CheckPartnerDetails(models.TransientModel):
    _name = 'trilab.check.partner.details'
    _description = 'Trilab Check Partner Details Wizard'

    check_id = fields.Many2one('trilab.check.partner')

    gus_selected_id = fields.Many2one('trilab.check.partner.gus', domain="[('details_id', '=', id)]")
    gus_selection_ids = fields.One2many('trilab.check.partner.gus', 'details_id')

    partner_id = fields.Many2one('res.partner')

    name = fields.Char(string='Old Name', related='partner_id.name')
    vat = fields.Char(string='Old VAT', related='partner_id.vat')

    x_name = fields.Char(related='gus_selected_id.name')
    x_vat = fields.Char(related='gus_selected_id.vat')

    street = fields.Char(string='Old Street', related='partner_id.street')
    street2 = fields.Char(string='Old Street2', related='partner_id.street2')
    zip = fields.Char(string='Old Zip', related='partner_id.zip')
    city = fields.Char(string='Old City', related='partner_id.city')
    state_id = fields.Many2one('res.country.state', string='Old State', related='partner_id.state_id')
    phone = fields.Char(string='Old Phone', related='partner_id.phone')
    email = fields.Char(string='Old Email', related='partner_id.email')

    x_street = fields.Char(related='gus_selected_id.street')
    x_street2 = fields.Char(related='gus_selected_id.street2')
    x_zip = fields.Char(related='gus_selected_id.zip')
    x_city = fields.Char(related='gus_selected_id.city')
    x_state_id = fields.Many2one('res.country.state', related='gus_selected_id.state_id')
    x_phone = fields.Char(related='gus_selected_id.phone')
    x_email = fields.Char(related='gus_selected_id.email')

    regon = fields.Char(string='Old REGON', related='partner_id.regon')
    krs = fields.Char(string='Old KRS/Reg. No', related='partner_id.krs')

    x_regon = fields.Char(related='gus_selected_id.regon')
    x_krs = fields.Char(related='gus_selected_id.krs')

    x_pl_gus_update_date = fields.Date(related='partner_id.x_pl_gus_update_date')

    user_id = fields.Many2one('res.users', related='partner_id.user_id')
    category_id = fields.Many2many('res.partner.category', related='partner_id.category_id')
    company_id = fields.Many2one('res.company', related='partner_id.company_id')
    is_company = fields.Boolean(related='partner_id.is_company')
    parent_id = fields.Many2one('res.partner', related='partner_id.parent_id')
    active = fields.Boolean(related='partner_id.active')

    x_pl_nip_state = fields.Char(related='partner_id.x_pl_nip_state')
    x_pl_nip_check_date = fields.Date(related='partner_id.x_pl_nip_check_date')

    x_pl_vies_state = fields.Selection(related='partner_id.x_pl_vies_state')
    x_pl_vies_check_date = fields.Date(related='partner_id.x_pl_vies_check_date')

    is_error = fields.Boolean(compute='compute_is_error')
    is_warning = fields.Boolean(compute='compute_is_error')
    error_type = fields.Char()
    error_message = fields.Char()

    # @api.depends('gus_selection_ids')
    # def compute_gus_selected_id(self):
    #     for rec in self:
    #         if len(rec.gus_selection_ids) == 1:
    #             rec.gus_selected_id = rec.gus_selection_ids[0]

    @api.depends('error_type', 'gus_selected_id')
    def compute_is_error(self):
        for rec in self:
            rec.is_error = rec.is_warning = False

            if rec.error_type:
                if rec.error_type == 'gus_multiple':
                    if not self.gus_selected_id:
                        rec.is_warning = True
                elif rec.error_type != 'gus_update':
                    rec.is_error = True
            rec.flush()

    def update_partner(self):
        if self.partner_id and self.gus_selected_id:
            self.error_type = None
            self.x_pl_gus_update_date = fields.Date.today()

            keys = ['name', 'street', 'street2', 'city', 'zip', 'phone', 'email', 'website',
                    'regon', 'krs', 'x_pl_gus_update_date', 'vat', 'lang']

            data = {key: getattr(self.gus_selected_id, key) for key in keys}
            data['state_id'] = self.gus_selected_id.state_id.id if self.gus_selected_id.state_id else None
            data['country_id'] = self.gus_selected_id.country_id.id if self.gus_selected_id.country_id else None

            # no override if key is True
            for key in ['email', 'phone', 'website']:
                if getattr(self.partner_id, key):
                    data.pop(key, None)

            self.partner_id.write(data)
            self.partner_id.message_post(body=_('Partner data updated from GUS.'))
            self.partner_id.flush()

    def close_popup(self):
        return {}


class CheckPartner(models.TransientModel):
    _name = 'trilab.check.partner'
    _description = 'Trilab Check Partner Wizard'

    check_ids = fields.One2many('trilab.check.partner.details', inverse_name='check_id')
    mode = fields.Selection([('gus', 'GUS'), ('nip', 'NIP'), ('vies', 'VIES')])
    errors_count = fields.Integer(compute='_compute_errors_count', store=False)

    @api.depends('check_ids', 'check_ids.error_type')
    def _compute_errors_count(self):
        for rec in self:
            rec.errors_count = len(rec.check_ids.filtered(lambda r: r.error_type))

    @api.onchange('check_ids')
    def onchange_check_ids(self):
        for rec in self.check_ids:
            if rec.error_type in ['gus_multiple', 'gus_update'] and rec.gus_selected_id:
                rec.update_partner()
