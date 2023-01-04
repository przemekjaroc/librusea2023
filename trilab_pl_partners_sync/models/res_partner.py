import re
from datetime import date
from functools import reduce
from types import SimpleNamespace
from typing import Optional
from urllib.parse import urljoin

import requests
import zeep
import zeep.exceptions
from stdnum.eu import vat as std_eu_vat
from stdnum.pl import nip as std_pl_nip, pesel as std_pl_pesel

# noinspection PyProtectedMember
from odoo import api, fields, models, _
from odoo.exceptions import ValidationError, UserError
from .gus_regon import GusClient, ReportType, GusException, EntityType


MF_GOV_PL_WSDL = 'https://sprawdz-status-vat.mf.gov.pl/?wsdl'

KRD_ENV = {
    'prod': 'https://services.krd.pl/Chase/3.1/Search.svc?WSDL',
    'test': 'https://demo.krd.pl/Chase/3.1/Search.svc?WSDL',
}

VIES_ERRORS = {
    'INVALID_INPUT': 'The provided CountryCode is invalid or the VAT number is empty.',
    'GLOBAL_MAX_CONCURRENT_REQ': 'Your Request for VAT validation has not been processed. '
                                 'The maximum number of concurrent requests has been reached.',
    'SERVICE_UNAVAILABLE': 'An error was encountered either at the network level or the Web application level.',
    'MS_UNAVAILABLE': 'The application at the Member State is not replying or not available.',
    'TIMEOUT': 'The application did not receive a reply within the allocated time period.',
}

GUS_REGON_FIELD_MAP = {
    'name': 'nazwa',
    'street': 'adSiedzUlica_Nazwa',
    'street_number': 'adSiedzNumerNieruchomosci',
    'unit_number': 'adSiedzNumerLokalu',
    'street2': 'adSiedzNietypoweMiejsceLokalizacji',
    'state_id': 'adSiedzWojewodztwo_Nazwa',
    'zip': 'adSiedzKodPocztowy',
    'phone': 'numerTelefonu',
    'phone_internal': 'numerWewnetrznyTelefonu',
    'email': 'adresEmail',
    'website': 'adresStronyinternetowej',
    'krs': 'numerWRejestrzeEwidencji',
    'city': 'adSiedzMiejscowosc_Nazwa',
}

GUS_REGON_PREFIX_MAP = {
    EntityType.OsFizyczna: 'fiz_{}',
    EntityType.OsPrawna: 'praw_{}',
    EntityType.JednostkaLokalnaOsFizycznej: 'lokfiz_{}',
    EntityType.JednostkaLokalnaOsPrawnej: 'lokpraw_{}',
}

MF_WL_PROD_URL = 'https://wl-api.mf.gov.pl/'


class ResPartner(models.Model):
    _inherit = 'res.partner'

    x_pl_nip_state = fields.Char(
        string='NIP Status',
        tracking=True,
        help="""
    Status odpowiada poniższej liście:
    N - Podmiot o podanym identyfikatorze podatkowym NIP nie jest zarejestrowany jako podatnik VAT
    C - Podmiot o podanym identyfikatorze podatkowym NIP jest zarejestrowany jako podatnik VAT czynny
    Z - Podmiot o podanym identyfikatorze podatkowym NIP jest zarejestrowany jako podatnik VAT zwolniony
    I - Błąd zapytania - Nieprawidłowy Numer Identyfikacji Podatkowej
    D - Błąd zapytania - Data spoza ustalonego zakresu
    X - Usługa nieaktywna""",
    )

    x_pl_vies_state = fields.Selection(
        [('valid', 'Valid'), ('invalid', 'Invalid'), ('no_info', 'No information')],
        string='VIES Status',
        default='no_info',
    )

    x_pl_nip_check_date = fields.Date(string='NIP Check Date')
    x_pl_gus_update_date = fields.Date(string='GUS Update Date')
    x_pl_vies_check_date = fields.Date(string='VIES Check Date')

    x_pl_business_type = fields.Selection(
        [
            (EntityType.OsFizyczna.value, 'Osoba fizyczna'),
            (EntityType.OsPrawna.value, 'Osoba prawna'),
            (EntityType.JednostkaLokalnaOsFizycznej.value, 'Jednostka lokalna osoby fizycznej'),
            (EntityType.JednostkaLokalnaOsPrawnej.value, 'Jednostka lokalna osoby prawnej'),
        ]
    )

    regon = fields.Char(string='REGON')
    krs = fields.Char(string='KRS/NR Ew.')
    pesel = fields.Char(string='PESEL')

    x_pl_is_poland = fields.Boolean(compute='x_pl_compute_country_flag', store=False)
    x_pl_is_europe = fields.Boolean(compute='x_pl_compute_country_flag', store=False)

    x_pl_enable_gus = fields.Boolean(compute='x_pl_enable_gus_krd', store=False)
    x_pl_enable_krd = fields.Boolean(compute='x_pl_enable_gus_krd', store=False)

    def x_pl_check_vies_cron(self):
        eu_countries_no_pl = (
            self.env.ref('base.europe').country_ids.filtered_domain([('id', '!=', self.env.ref('base.pl').id)]).ids
        )

        partners = self.search(
            [
                ('is_company', '=', True),
                ('country_id', 'in', eu_countries_no_pl),
                ('vat', '!=', False),
                ('user_id', '!=', False),
            ]
        )

        for salesperson in partners.mapped('user_id'):
            for partner in partners.filtered(lambda p: p.user_id.id == salesperson.id).with_context(
                {'lang': salesperson.lang}
            ):

                try:
                    partner.x_pl_check_vies()
                except ValidationError as exception:
                    partner.message_post(
                        body=_('Error while checking VIES: %s', str(exception.args[0])),
                        partner_ids=[salesperson.partner_id.id],
                    )

                if partner.x_pl_vies_state != 'valid':
                    partner.message_post(body=_('Invalid VIES'), partner_ids=[salesperson.partner_id.id])

    @api.depends('country_id')
    def x_pl_compute_country_flag(self):
        for partner in self:
            partner.x_pl_is_poland = partner.country_id.id == self.env.ref('base.pl').id
            partner.x_pl_is_europe = (
                partner.x_pl_is_poland or partner.country_id.id in self.env.ref('base.europe').country_ids.ids
            )

    # @api.depends('company_id')
    def x_pl_enable_gus_krd(self):
        for partner in self:
            company_id = partner.company_id or self.env.company
            partner.x_pl_enable_gus = company_id.x_pl_enable_gus
            partner.x_pl_enable_krd = company_id.x_pl_enable_krd

    def x_pl_check_vies(self, raise_exception=True):
        errors = {}
        response = None
        for partner in self:
            try:
                # fully numeric VAT - add PL in front
                vat = f'PL{partner.vat}' if partner.vat and partner.vat.isdigit() else partner.vat
                # first validate internally - before calling vies
                std_eu_vat.validate(vat)
                # call vies
                response = std_eu_vat.check_vies(vat)

            except std_eu_vat.ValidationError as exception:
                if str(exception) in VIES_ERRORS:
                    error = VIES_ERRORS[str(exception)]
                else:
                    error = str(exception)

                if raise_exception:
                    raise ValidationError(error)
                else:
                    errors[partner.id] = {'error_type': 'vies_error', 'error_message': error}

            partner.x_pl_vies_state = 'valid' if response and response['valid'] else 'invalid'
            partner.x_pl_vies_check_date = fields.Date.today()

        return errors

    @api.constrains('pesel')
    def x_pl_constrains_pesel(self):
        for partner in self.filtered(lambda p: p.pesel):
            try:
                std_pl_pesel.validate(partner.pesel)
            except std_pl_pesel.ValidationError as error:
                raise ValidationError(str(error))

    def _x_pl_parse_gus_data(self, data: dict, company_type: EntityType, for_model=False):
        poland_id = self.env.ref('base.pl')

        fields_map = {k: GUS_REGON_PREFIX_MAP[company_type].format(v) for k, v in GUS_REGON_FIELD_MAP.items()}

        company_data = SimpleNamespace(**{mt: data.get(mf) for mt, mf in fields_map.items()})

        # mapping exception
        if company_type == EntityType.OsFizyczna:
            fields_map['krs'] = f'fizC_{GUS_REGON_FIELD_MAP["krs"]}'

        # data cleanup
        if company_data.zip and '-' not in company_data.zip:
            company_data.zip = f'{company_data.zip[:2]}-{company_data.zip[2:]}'

        if company_data.street_number:
            if company_data.unit_number:
                company_data.street_number = f'{company_data.street_number}/{company_data.unit_number}'
                company_data.unit_number = None
        else:
            company_data.street_number = ''

        if not company_data.street:
            if company_data.city:
                company_data.street = company_data.city
                post_city = data.get(GUS_REGON_PREFIX_MAP[company_type].format('adSiedzMiejscowoscPoczty_Nazwa'))
                if post_city:
                    company_data.city = post_city
            else:
                company_data.street = ''

        if company_data.street_number:
            company_data.street = f'{company_data.street} {company_data.street_number}'
            company_data.street_number = None

        if company_data.phone_internal:
            company_data.phone = _('%s i. %s', company_data.phone, company_data.phone_internal)
            company_data.phone_internal = None

        # noinspection PyProtectedMember
        if company_data.state_id and isinstance(company_data.state_id, str):
            state_id = poland_id.state_ids.search([('name', '=', company_data.state_id.lower())], limit=1)
            if state_id:
                company_data.state_id = state_id.id if for_model else {'id': state_id.id, 'name': state_id.name}
            else:
                company_data.state_id = None

        output_dict = {
            'x_pl_gus_update_date': fields.Date.today(),
            'x_pl_business_type': company_type.value,
            'lang': self.env['res.lang'].search([('iso_code', 'ilike', poland_id.code)], limit=1).code,
        }

        if for_model:
            output_dict['country_id'] = poland_id.id
        else:
            output_dict['country_id'] = {'id': poland_id.id, 'display_name': poland_id.display_name}

        for field in fields_map.keys():
            value = getattr(company_data, field)
            if value:
                output_dict[field] = value

        return output_dict

    @staticmethod
    def x_sanitize_nip(nip):
        if nip:
            return re.sub(r'\D', '', nip)
        return nip

    def _x_pl_get_gus_data(self, nip=None, for_model=False, raise_exception=False):
        if not nip:
            raise ValidationError(_('Please provide NIP'))

        try:
            std_pl_nip.validate(nip)
        except std_pl_nip.ValidationError as e:
            if raise_exception:
                raise ValidationError(e)
            else:
                return None

        api_key = self.env['ir.config_parameter'].sudo().get_param('trilab_gusregon.x_pl_gus_api_key')

        if not api_key:
            raise ValidationError(_('Please set GUS API key in General Settings'))

        gus = GusClient(api_key=api_key)

        try:
            response = gus.get_partners_data(nip=self.x_sanitize_nip(nip))
        except GusException:
            raise ValidationError(_('Invalid data for NIP'))

        companies_data = []

        if not response:
            return companies_data

        if isinstance(response, dict):
            response = [response]

        for company in response:
            company_type = EntityType(company.get('Typ'))
            silos_id = company.get('SilosID', '0')
            report = None

            if company_type == EntityType.OsFizyczna:
                if silos_id == '1':
                    report = ReportType.OsFizycznaDzialalnoscCeidg
                elif silos_id == '2':
                    report = ReportType.OsFizycznaDzialalnoscRolnicza
                elif silos_id == '3':
                    report = ReportType.OsFizycznaDzialalnoscPozostala
                elif silos_id == '4':
                    report = ReportType.OsFizycznaDzialalnoscSkreslona

            elif company_type == EntityType.JednostkaLokalnaOsFizycznej and silos_id in ('1', '2', '3'):
                report = ReportType.JednLokalnaOsFizycznej

            elif company_type == EntityType.OsPrawna and silos_id == '6':
                report = ReportType.OsPrawna

            elif company_type == EntityType.JednostkaLokalnaOsPrawnej and silos_id == '6':
                report = ReportType.JednLokalnaOsPrawnej

            if not report:
                raise ValidationError(_('invalid combination of Type(%s) and SilosID(%s)', company_type, silos_id))

            regon = company.get('Regon')
            response = gus.get_full_report(regon, report, raise_exception=False)

            if report == ReportType.OsFizycznaDzialalnoscCeidg:
                for key in GUS_REGON_FIELD_MAP.values():
                    ceidg_key = f'fizC_{key}'
                    if ceidg_key in response:
                        response[f'fiz_{key}'] = response[ceidg_key]

            output_dict = self._x_pl_parse_gus_data(response, company_type, for_model=for_model)

            output_dict['regon'] = regon
            output_dict['vat'] = nip or company.get('Nip')

            companies_data.append(output_dict)

        return companies_data

    def x_pl_update_gus_data(self):
        errors = {}

        for partner in self:
            if partner.country_id.id != self.env.ref('base.pl').id:
                raise ValidationError(_('Customer must be from Poland'))

            if not partner.vat:
                raise ValidationError(_('VAT is required'))

            # noinspection PyProtectedMember
            output = partner._x_pl_get_gus_data(nip=partner.vat, for_model=True)

            if output:
                errors[partner.id] = {
                    'error_type': 'gus_multiple' if len(output) > 1 else 'gus_update',
                    'error_message': _('Multiple Records data found in GUS please select company.')
                    if len(output) > 1
                    else _('Please Check Update Details'),
                    'records': output,
                }
            else:
                errors[partner.id] = {'error_type': 'gus_invalid_vat', 'error_message': _('NIP is not valid')}
        return errors

    def x_pl_check_mf_nip(self, raise_exception=True):
        poland = self.env.ref('base.pl')
        errors = {}

        for partner in self:
            # noinspection PyBroadException
            try:
                if partner.country_id.id != poland.id or not partner.vat:
                    raise ValidationError('not .pl')

                client = zeep.Client(MF_GOV_PL_WSDL)
                response = client.service.SprawdzNIP(ResPartner.x_pl_check_nip(partner.vat))
                partner.x_pl_nip_state = response['Kod']
                partner.x_pl_nip_check_date = fields.Date.today()

                partner.message_post(
                    body=_('Ministry of Finance NIP Validity checked (%s)', partner.x_pl_nip_state)
                )

            except ValidationError:
                if raise_exception:
                    raise
                else:
                    errors[partner.id] = {'error_type': 'invalid_nip', 'error_message': _('Invalid VAT number')}

            except std_pl_nip.ValidationError as e:
                if raise_exception:
                    raise ValidationError(e)
                else:
                    errors[partner.id] = {'error_type': 'invalid_nip', 'error_message': str(e)}

            except zeep.exceptions.Error as e:
                if raise_exception:
                    raise
                else:
                    partner.x_pl_nip_state = False
                    partner.x_pl_nip_check_date = False

                    errors[partner.id] = {'error_type': 'invalid_nip', 'error_message': str(e)}

        return errors

    def x_pl_check_krd(self):
        self.ensure_one()

        company = self.env.user.company_id
        if not company.x_pl_krd_login or not company.x_pl_krd_pass:
            raise ValidationError(_('Please set KRD login and password in company settings'))

        if not self.country_id or self.country_id.code != 'PL':
            raise ValidationError(_('The company/customer must be registered in Poland'))

        if self.is_company and not self.vat:
            raise ValidationError(_('VAT is required'))

        if not self.is_company and not self.pesel:
            raise ValidationError(_('PESEL is required'))

        client = zeep.Client(KRD_ENV[company.x_pl_krd_env])

        auth_header = zeep.xsd.Element(
            '{https://krd.pl/Authorization}Authorization',
            zeep.xsd.ComplexType(
                [
                    zeep.xsd.Element('{https://krd.pl/Authorization}AuthorizationType', zeep.xsd.String()),
                    zeep.xsd.Element('{https://krd.pl/Authorization}Login', zeep.xsd.String()),
                    zeep.xsd.Element('{https://krd.pl/Authorization}Password', zeep.xsd.String()),
                ]
            ),
        )

        auth_value = auth_header(
            AuthorizationType='LoginAndPassword', Login=company.x_pl_krd_login, Password=company.x_pl_krd_pass
        )

        if self.is_company:
            response = client.service.SearchNonConsumer(NumberType='TaxId', Number=self.vat, _soapheaders=[auth_value])

        else:
            response = client.service.SearchConsumer(
                NumberType='Pesel',
                Number=self.pesel,
                AuthorizationDate=fields.Datetime.now().strftime('%Y-%m-%dT%H:%M:%S'),
                _soapheaders=[auth_value],
            )

        response = dict(
            Summary=response['body']['DisclosureReport']['Summary'],
            PositiveInformationSummary=response['body']['DisclosureReport']['PositiveInformationSummary'],
        )

        # noinspection PyProtectedMember
        self.message_post(body=self.env['ir.qweb']._render('trilab_pl_partners_sync.krd_result', response))

    @api.model
    def autocomplete(self, query):
        parsed_nip = ResPartner._x_pl_check_string(query)
        if parsed_nip:
            return self._x_pl_get_gus_data(nip=parsed_nip) or []
        else:
            return super(ResPartner, self).autocomplete(query)

    @api.model
    def read_by_vat(self, vat):
        valid_pl_vat = ResPartner.x_pl_check_nip(vat)
        if valid_pl_vat:
            return self._x_pl_get_gus_data(nip=valid_pl_vat)
        else:
            return super(ResPartner, self).read_by_vat(vat)

    @api.model
    def enrich_company(self, company_domain, partner_gid, vat, timeout=15):
        account = self.env['iap.account'].get('partner_autocomplete')
        if not account.account_token:
            return {}
        else:
            return super(ResPartner, self).enrich_company(company_domain, partner_gid, vat, timeout)

    @staticmethod
    def _x_pl_check_string(nip: str) -> Optional[str]:
        nip = nip.replace('-', '').replace(' ', '')
        matcher = re.match(r'^((?i:PL)?(?P<nip>\d{10}))$', nip)
        if matcher:
            return matcher.groupdict()['nip']

    @staticmethod
    def x_pl_check_nip(nip: str) -> str:
        value = ResPartner._x_pl_check_string(nip)
        return value if value and std_pl_nip.is_valid(value) else None

    # noinspection PyMethodMayBeStatic
    def _x_pl_check_action(self, record_id, title):
        return {
            'name': title,
            'type': 'ir.actions.act_window',
            'view_mode': 'form',
            'res_model': 'trilab.check.partner',
            'res_id': record_id,
            'target': 'new',
        }

    def x_pl_update_gus_action(self):
        poland = self.env.ref('base.pl')
        partners = self.filtered(lambda partner: partner.country_id.id == poland.id and partner.vat)

        errors = partners.x_pl_update_gus_data()

        if self.env.context.get('no_confirm', False) and not errors:
            return {}

        if len(partners) > 1:
            record = [
                (
                    0,
                    0,
                    {
                        'partner_id': p.id,
                        'gus_selection_ids': [
                            (0, 0, {'partner_id': p, **r})
                            for p, d in errors.items()
                            if 'records' in d
                            for r in d.get('records')
                        ],
                        'error_type': errors.get(p.id, {}).get('error_type'),
                        'error_message': errors.get(p.id, {}).get('error_message'),
                    },
                )
                for p in partners
            ]

            record = self.env['trilab.check.partner'].sudo().create({'check_ids': record, 'mode': 'gus'})

            # noinspection PyProtectedMember
            return partners._x_pl_check_action(record_id=record.id, title=_('Updated data from GUS'))

        elif len(partners) == 1:
            record = (
                self.env['trilab.check.partner.details']
                .sudo()
                .create(
                    {
                        'partner_id': partners.id,
                        'gus_selection_ids': [
                            (0, 0, {'partner_id': p, **r})
                            for p, d in errors.items()
                            if 'records' in d
                            for r in d.get('records')
                        ],
                        'error_type': errors.get(partners.id, {}).get('error_type'),
                        'error_message': errors.get(partners.id, {}).get('error_message'),
                    }
                )
            )

            if len(record.gus_selection_ids) == 1:
                record.gus_selected_id = record.gus_selection_ids[0]

            return {
                'type': 'ir.actions.act_window',
                'res_model': 'trilab.check.partner.details',
                'views': [[False, 'form']],
                'view_mode': 'form',
                'res_id': record.id,
                'target': 'new',
            }

        else:
            raise UserError(_('Please select partner'))

    # noinspection DuplicatedCode
    def x_pl_check_nip_action(self):
        poland = self.env.ref('base.pl')
        partners = self.filtered(lambda partner: partner.country_id.id == poland.id and partner.vat)

        errors = partners.x_pl_check_mf_nip(raise_exception=False)

        if self.env.context.get('no_confirm', False) and not errors:
            return {}

        rec = [
            (
                0,
                0,
                {
                    'partner_id': p.id,
                    'error_type': errors.get(p.id, {}).get('error_type'),
                    'error_message': errors.get(p.id, {}).get('error_message'),
                },
            )
            for p in partners
        ]

        record = self.env['trilab.check.partner'].sudo().create({'check_ids': rec, 'mode': 'nip'})

        # noinspection PyProtectedMember
        return partners._x_pl_check_action(record_id=record.id, title=_('MF VAT Validation Results'))

    # noinspection DuplicatedCode
    def x_pl_check_vies_action(self):
        partners = self.filtered(lambda partner: partner.vat)
        errors = partners.x_pl_check_vies(raise_exception=False)

        if self.env.context.get('no_confirm', False) and not errors:
            return {}

        rec = [
            (
                0,
                0,
                {
                    'partner_id': p.id,
                    'error_type': errors.get(p.id, {}).get('error_type'),
                    'error_message': errors.get(p.id, {}).get('error_message'),
                },
            )
            for p in partners
        ]

        record = self.env['trilab.check.partner'].sudo().create({'check_ids': rec, 'mode': 'vies'})

        # noinspection PyProtectedMember
        return partners._x_pl_check_action(record_id=record.id, title=_('VIES Validation Results'))

    def x_pl_get_bank_accounts(self):
        self.ensure_one()

        nip = self.x_pl_check_nip(self.vat)
        if not nip:
            raise ValidationError(_('Invalid VAT number'))

        # https://wl-api.mf.gov.pl/api/search/nip/{nip}?date={date}
        url = reduce(urljoin, [MF_WL_PROD_URL, 'api/search/nip/', nip])
        response = requests.get(url, params={'date': date.today().isoformat()})
        response_json = response.json()
        if response.ok:
            bank_accounts = (response_json.get('result', {}).get('subject') or {}).get('accountNumbers', [])
            if bank_accounts:
                bank_accounts = list(
                    filter(lambda x: f'PL{x}' not in self.bank_ids.mapped('sanitized_acc_number'), bank_accounts)
                )
                if len(bank_accounts) == 1:
                    self.bank_ids.create({'acc_number': bank_accounts[0], 'partner_id': self.id})
                    self.message_post(
                        body=_('Bank account added from Whitelist of Ministry of Finance: %s') % bank_accounts[0]
                    )
                elif len(bank_accounts) > 1:
                    wizard = self.env['trilab.wl.wizard'].sudo().create({'partner_id': self.id})

                    wizard.write(
                        {
                            'banks_ids': [
                                (0, 0, {'wl_wizard_id': wizard.id, 'acc_number': bank_account})
                                for bank_account in bank_accounts
                            ]
                        }
                    )

                    return {
                        'name': _('Select Whitelist Bank Accounts To Save'),
                        'type': 'ir.actions.act_window',
                        'res_model': 'trilab.wl.wizard',
                        'res_id': wizard.id,
                        'view_mode': 'form',
                        'target': 'new',
                    }
            else:
                raise ValidationError(_('No bank accounts found for VAT id: %s') % nip)
        else:
            raise ValidationError(_('Error accrued: %s') % response_json)
