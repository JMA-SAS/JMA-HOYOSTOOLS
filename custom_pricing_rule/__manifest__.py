{
    'name': 'Custom Pricing Rule',
    'version': '1.0',
    'summary': 'Adds brand, origin, and tag-based rules to pricing lists',
    'author': "PETI Soluciones Productivas",
    'website': "http://www.peti.com.co",
    'category': 'Sales',
    'depends': ['base', 'sale', 'product', 'product_brand'],  # Dependencias necesarias
    'data': [
        'views/product_pricelist_items_views.xml'
    ],
    'installable': True,
    'application': False,
    'auto_install': False,
}
