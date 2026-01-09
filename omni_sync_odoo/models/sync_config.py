from odoo import models, fields, api
from odoo.exceptions import ValidationError

class SyncConfig(models.Model):
    _name = 'omni.sync.config'
    _description = 'Parametrización de Conexiones B4B SYNC'
    _rec_name = 'name'

    name = fields.Char(string='Nombre', required=True)
    
    # Configuración del servidor remoto (Origen de datos)
    remote_url = fields.Char(string='URL Servidor Origen (Remoto)', required=True, help='URL de donde se traerán las imágenes o se enviarán datos')
    remote_database = fields.Char(string='Base de Datos Origen', required=True)
    remote_username = fields.Char(string='Usuario Origen', required=True)
    remote_password = fields.Char(string='Contraseña Origen', required=True)
    
    # Checkboxes de funcionalidad
    sync_products = fields.Boolean(string='Sincronizar Productos', default=False)
    sync_images = fields.Boolean(string='Sincronizar Imágenes', default=True)
    sync_pricelists = fields.Boolean(string='Sincronizar Listas de Precios', default=False)
    sync_sales = fields.Boolean(string='Sincronizar Ventas', default=False)
    sync_purchases = fields.Boolean(string='Sincronizar Compras (desde Facturas)', default=False)
    
    # Configuración específica de imágenes
    brands_to_sync = fields.Text(string='Marcas a Sincronizar', default='TOTAL', help='Separadas por comas')
    batch_size = fields.Integer(string='Tamaño de Lote', default=50)
    timeout = fields.Integer(string='Timeout (segundos)', default=120)
    
    # Configuración específica de compras
    auto_confirm_po = fields.Boolean(string='Confirmar OC Automáticamente', default=False)
    
    active = fields.Boolean(string='Activo', default=True)
    
    # Estadísticas para el tablero
    last_sync_date = fields.Datetime(string='Última Sincronización')
    total_synced_products = fields.Integer(string='Productos Sincronizados', default=0)
    total_synced_images = fields.Integer(string='Imágenes Sincronizadas', default=0)
    total_synced_pricelists = fields.Integer(string='Listas de Precios Sincronizadas', default=0)
    total_synced_sales = fields.Integer(string='Ventas Sincronizadas', default=0)
    total_synced_purchases = fields.Integer(string='Compras Sincronizadas', default=0)

    def _get_xmlrpc_proxies(self):
        self.ensure_one()
        import xmlrpc.client

        url = (self.remote_url or '').strip().rstrip('/')

        if not url.startswith('http'):
            url = 'https://' + url

        common = xmlrpc.client.ServerProxy(
            f"{url}/xmlrpc/2/common",
            allow_none=True
        )

        models = xmlrpc.client.ServerProxy(
            f"{url}/xmlrpc/2/object",
            allow_none=True
        )

        return common, models


    def update_stats(self):
        """Actualiza las estadísticas de forma manual o tras una sincronización para no ralentizar el tablero"""
        for record in self:
            # Imágenes y Precios desde logs
            log_data = self.env['sync.pictures.log'].search([('config_id', '=', record.id)])
            
            # Ventas sincronizadas hacia este remoto (usando el log o referencia)
            sales_count = self.env['sale.order'].search_count([
                ('is_synced', '=', True),
                ('sync_log', 'like', f'<td>{record.remote_database}</td>')
            ])
            
            # Compras creadas desde facturas para este remoto
            purchases_count = self.env['purchase.order'].search_count([
                ('partner_ref', 'like', 'INV/'),
                ('message_ids.body', 'like', f'[{record.name}]')
            ])

            record.write({
                'total_synced_products': record._get_remote_product_count(),
                'total_synced_images': sum(log_data.mapped('products_synced')),
                'total_synced_pricelists': sum(log_data.mapped('pricelists_synced')),
                'total_synced_sales': sales_count,
                'total_synced_purchases': purchases_count,
            })
    
    def _get_remote_product_count(self):
        self.ensure_one()

        try:
            # Obtener proxies XML-RPC centralizados
            common, models = self._get_xmlrpc_proxies()

            # Autenticación
            uid = common.authenticate(
                self.remote_database,
                self.remote_username,
                self.remote_password,
                {}
            )

            if not uid:
                raise Exception('No fue posible autenticar con la base de datos remota')

            # Contar productos (product.template)
            product_count = models.execute_kw(
                self.remote_database,
                uid,
                self.remote_password,
                'product.template',
                'search_count',
                [[('active', '=', True)]]
            )

            # Guardar resultado en el modelo (si existe el campo)
            if hasattr(self, 'remote_product_count'):
                self.remote_product_count = product_count

            return product_count

        except Exception as e:
            _logger.error(
                'Error obteniendo cantidad de productos remotos [%s]: %s',
                self.remote_url,
                str(e),
                exc_info=True
            )
            raise UserError(
                f'Error al obtener la cantidad de productos remotos:\n{str(e)}'
            )

    def _sync_products_from_remote(self, batch_size=100):
        self.ensure_one()

        uid, models_proxy = self._get_remote_connection()

        Product = self.env['product.product']
        offset = 0
        total_created = 0
        total_updated = 0

        while True:
            try:
                remote_products = models_proxy.execute_kw(
                    self.remote_db,
                    uid,
                    self.remote_password,
                    'product.product',
                    'search_read',
                    [[('active', '=', True)]],
                    {
                        'fields': [
                            'name',
                            'default_code',
                            'barcode',
                            'list_price',
                            'standard_price',
                            'type'
                        ],
                        'limit': batch_size,
                        'offset': offset
                    }
                )

            except Exception as e:
                raise UserError(_(
                    "Error obteniendo productos remotos:\n%s"
                ) % str(e))

            if not remote_products:
                break

            for rp in remote_products:
                if not rp.get('default_code'):
                    continue  # clave para evitar duplicados malos

                local_product = Product.search(
                    [('default_code', '=', rp['default_code'])],
                    limit=1
                )

                vals = {
                    'name': rp['name'],
                    'default_code': rp['default_code'],
                    'barcode': rp.get('barcode'),
                    'list_price': rp.get('list_price', 0.0),
                    'standard_price': rp.get('standard_price', 0.0),
                    'type': rp.get('type', 'product'),
                }

                if local_product:
                    local_product.write(vals)
                    total_updated += 1
                else:
                    Product.create(vals)
                    total_created += 1

            offset += batch_size

        return {
            'created': total_created,
            'updated': total_updated
        }

    def _create_or_update_product(self, remote_product_data):
        """Crea o actualiza un producto en la base local"""
        product_obj = self.env['product.product']
        
        # Buscar producto por referencia interna o código de barras
        domain = []
        if remote_product_data.get('default_code'):
            domain.append(('default_code', '=', remote_product_data['default_code']))
        elif remote_product_data.get('barcode'):
            domain.append(('barcode', '=', remote_product_data['barcode']))
        else:
            # Si no hay referencia, buscar por nombre
            domain.append(('name', '=', remote_product_data['name']))
        
        existing_product = product_obj.search(domain, limit=1)
        
        vals = {
            'name': remote_product_data['name'],
            'default_code': remote_product_data.get('default_code'),
            'list_price': remote_product_data.get('list_price', 0.0),
            'standard_price': remote_product_data.get('standard_price', 0.0),
            'barcode': remote_product_data.get('barcode'),
        }
        
        if existing_product:
            existing_product.write(vals)
        else:
            product_obj.create(vals)

    @api.constrains('remote_url')
    def _check_urls(self):
        for record in self:
            if record.remote_url and not record.remote_url.startswith('http'):
                raise ValidationError('La URL debe comenzar con http:// o https://')

    def test_connection(self):
        try:
            common, _models = self._get_xmlrpc_proxies()
            uid = common.authenticate(
                self.remote_database,
                self.remote_username,
                self.remote_password,
                {}
            )
            if uid:
                return {
                    'type': 'ir.actions.client',
                    'tag': 'display_notification',
                    'params': {
                        'title': 'Éxito',
                        'message': 'Conexión exitosa con el servidor remoto',
                        'type': 'success',
                    }
                }
            else:
                raise Exception('Autenticación fallida')
        except Exception as e:
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': 'Error',
                    'message': f'Error de conexión: {str(e)}',
                    'type': 'danger',
                }
            }

    # --- Métodos para Acciones Planificadas ---

    def action_manual_sync(self):
        """Ejecuta la sincronización manualmente para este registro"""
        self.ensure_one()
        self.cron_sync_all(config_id=self.id, execution_type='manual')
        self.update_stats()
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Sincronización Iniciada',
                'message': f'Se ha ejecutado la sincronización para {self.name}',
                'type': 'success',
                'sticky': False,
            }
        }

    def action_sync_pricelists_to_remote(self):
        self.ensure_one()

        uid, models_proxy = self._get_remote_connection()

        Pricelist = self.env['product.pricelist']
        PricelistItem = self.env['product.pricelist.item']

        synced = 0
        updated = 0

        for pricelist in Pricelist.search([]):

            # Buscar lista remota por nombre
            remote_ids = models_proxy.execute_kw(
                self.remote_db,
                uid,
                self.remote_password,
                'product.pricelist',
                'search',
                [[('name', '=', pricelist.name)]],
                {'limit': 1}
            )

            pricelist_vals = {
                'name': pricelist.name,
                'currency_id': pricelist.currency_id.id,
                'active': pricelist.active,
            }

            # Crear o actualizar lista de precios
            if remote_ids:
                remote_pricelist_id = remote_ids[0]
                models_proxy.execute_kw(
                    self.remote_db,
                    uid,
                    self.remote_password,
                    'product.pricelist',
                    'write',
                    [[remote_pricelist_id], pricelist_vals]
                )
                updated += 1
            else:
                remote_pricelist_id = models_proxy.execute_kw(
                    self.remote_db,
                    uid,
                    self.remote_password,
                    'product.pricelist',
                    'create',
                    [pricelist_vals]
                )
                synced += 1

            # Eliminar reglas remotas existentes (evita inconsistencias)
            remote_items = models_proxy.execute_kw(
                self.remote_db,
                uid,
                self.remote_password,
                'product.pricelist.item',
                'search',
                [[('pricelist_id', '=', remote_pricelist_id)]]
            )

            if remote_items:
                models_proxy.execute_kw(
                    self.remote_db,
                    uid,
                    self.remote_password,
                    'product.pricelist.item',
                    'unlink',
                    [remote_items]
                )

            # Crear reglas de precios
            for item in PricelistItem.search([('pricelist_id', '=', pricelist.id)]):

                item_vals = {
                    'pricelist_id': remote_pricelist_id,
                    'applied_on': item.applied_on,
                    'min_quantity': item.min_quantity,
                    'compute_price': item.compute_price,
                    'fixed_price': item.fixed_price,
                    'percent_price': item.percent_price,
                    'price_discount': item.price_discount,
                    'price_surcharge': item.price_surcharge,
                    'price_round': item.price_round,
                    'price_min_margin': item.price_min_margin,
                    'price_max_margin': item.price_max_margin,
                }

                # Resolver dependencias (producto / plantilla / categoría)
                if item.product_id:
                    remote_product = models_proxy.execute_kw(
                        self.remote_db,
                        uid,
                        self.remote_password,
                        'product.product',
                        'search',
                        [[('default_code', '=', item.product_id.default_code)]],
                        {'limit': 1}
                    )
                    if remote_product:
                        item_vals['product_id'] = remote_product[0]
                    else:
                        continue

                if item.product_tmpl_id:
                    remote_template = models_proxy.execute_kw(
                        self.remote_db,
                        uid,
                        self.remote_password,
                        'product.template',
                        'search',
                        [[('name', '=', item.product_tmpl_id.name)]],
                        {'limit': 1}
                    )
                    if remote_template:
                        item_vals['product_tmpl_id'] = remote_template[0]
                    else:
                        continue

                if item.categ_id:
                    remote_category = models_proxy.execute_kw(
                        self.remote_db,
                        uid,
                        self.remote_password,
                        'product.category',
                        'search',
                        [[('name', '=', item.categ_id.name)]],
                        {'limit': 1}
                    )
                    if remote_category:
                        item_vals['categ_id'] = remote_category[0]
                    else:
                        continue

                models_proxy.execute_kw(
                    self.remote_db,
                    uid,
                    self.remote_password,
                    'product.pricelist.item',
                    'create',
                    [item_vals]
                )

        return {
            'status': 'success',
            'created_pricelists': synced,
            'updated_pricelists': updated
        }



    def cron_sync_all(self, config_id=None, execution_type='auto'):
        """Método principal llamado por el Cron o manualmente para múltiples clientes"""
        domain = [('active', '=', True)]
        if config_id:
            domain.append(('id', '=', config_id))
            
        configs = self.search(domain)
        for config in configs:
            # 0. Sincronización de Productos (Pull desde remoto)
            if config.sync_products:
                try:
                    config._sync_products_from_remote()
                except Exception as e:
                    pass
            
            # 1. Sincronización de Imágenes
            if config.sync_images:
                try:
                    wizard = self.env['sync.pictures.wizard'].sudo().with_context(active_test=False).create({
                        'config_id': config.id,
                        'execution_type': execution_type
                    })
                    wizard.action_sync_pictures()
                except Exception as e:
                    self.env['sync.pictures.log'].create({
                        'config_id': config.id,
                        'status': 'failed',
                        'error_message': f"Error en imágenes: {str(e)}",
                        'execution_type': execution_type,
                    })
            
            # 2. Sincronización de Listas de Precios
            if config.sync_pricelists:
                try:
                    config._sync_pricelists_to_remote(execution_type=execution_type)
                except Exception as e:
                    pass
            
            # 3. Sincronización de Ventas
            if config.sync_sales:
                orders = self.env['sale.order'].search([('state', '=', 'sale'), ('is_synced', '=', False)])
                for order in orders:
                    try:
                        order.with_context(omni_sync_config_id=config.id).action_sync_order()
                    except:
                        continue
            
            config.last_sync_date = fields.Datetime.now()
            config.update_stats()