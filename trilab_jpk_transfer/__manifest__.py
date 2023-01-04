# noinspection PyStatementEffect
{
    'name': 'Trilab JPK Transfer',
    'version': '1.13',
    'summary': 'Send JPK XML files to Ministry of Finance',
    'author': 'Trilab',
    'website': "https://trilab.pl",
    'support': 'odoo@trilab.pl',
    'category': 'Accounting',
    'depends': [
        'mail', 'trilab_jpk_base'
    ],
    'external_dependencies': {
        'python': ['cryptography']
    },
    'description': "This module allows to upload JPK (Jednolity Plik Kontrolny) files to MF.",
    'data': [
        'security/security.xml',
        'security/ir.model.access.csv',
        'data/data.xml',
        'views/jpk_document.xml',
        'views/jpk_file_part.xml',
        'views/jpk_settings.xml',
        'views/jpk_transfer.xml',
        'views/jpk_menu.xml',
    ],
    'images': [
        'static/description/banner.png'
    ],
    'post_init_hook': 'post_init_handler',
    'uninstall_hook': 'uninstall_handler',
    'installable': True,
    'auto_install': False,
    'application': True,
    'license': 'OPL-1',
    'price': 200.0,
    'currency': 'EUR'
}
