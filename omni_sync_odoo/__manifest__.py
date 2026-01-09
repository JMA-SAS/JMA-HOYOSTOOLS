{
    'name': 'B4B SYNC',
    'version': '17.0.1.1.0',
    'category': 'Tools',
    'summary': 'Sincronización unificada de Productos, Imágenes, Ventas y Compras entre instancias de Odoo',
    'description': '''
        Módulo unificado para la sincronización integral entre instancias de Odoo.
        
        Funcionalidades:
        - Sincronización de productos entre instancias.
        - Sincronización de imágenes de productos (basado en marcas).
        - Sincronización de Pedidos de Venta (Sale Orders).
        - Generación automática de Órdenes de Compra (Purchase Orders) desde Facturas.
        
        Configuración centralizada con opciones para habilitar/deshabilitar cada tipo de sincronización.
    ''',
    'author': 'Manus / Hoyostools',
    'website': 'https://www.hoyostools.com',
    'license': 'LGPL-3',
    'depends': [
        'base',
        'product',
        'sale_management',
        'purchase',
        'account',
        'utm',
    ],
    'data': [
        'security/security.xml',
        'security/ir.model.access.csv',
        'data/ir_cron_data.xml',
        'views/sync_dashboard_views.xml',
        'views/sync_config_views.xml',
        'views/sync_pictures_views.xml',
        'views/sale_order_views.xml',
        'views/account_move_views.xml',
        'views/product_pricelist_views.xml',
        'wizards/sync_pictures_wizard_views.xml',
        'views/menu_views.xml',
    ],
    'installable': True,
    'auto_install': False,
    'application': True,
}
