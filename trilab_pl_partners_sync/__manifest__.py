# noinspection PyStatementEffect
{
    'name': 'Trilab Partners Sync for Poland (GUS/WHITELIST/VIES/KRD)',

    'summary': 'Sync Partner data from GUS (Główny Urząd Statystyczny) '
               'and validate it with GUS/MF WHITELIST/EU VIES/KRD',
    'author': 'Trilab',
    'website': 'https://trilab.pl',
    'category': 'Accounting',
    'version': '3.4',
    'depends': [
        'base',
        'contacts',
        'partner_autocomplete'
    ],
    'external_dependencies': {
        'python': ['zeep', 'python-stdnum']
    },
    'data': [
        'security/ir.model.access.csv',
        'data/data.xml',
        'views/res_config_settings.xml',
        'views/res_partner.xml',
        'views/krd.xml',
        'wizard/partner_check.xml',
        'wizard/whitelist_accounts.xml',
    ],
    'images': [
        'static/description/banner.png'
    ],
    'assets': {
        'web.assets_backend': [
            'trilab_pl_partners_sync/static/src/js/autocomplete_core.js',
        ],
    },
    'installable': True,
    'auto_install': False,
    'application': True,
    'license': 'OPL-1',
    'price': 100.0,
    'currency': 'EUR'
}
