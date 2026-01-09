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
        """Consulta la cantidad de productos en la base de datos remota"""
        self.ensure_one()
        if not self.active or not self.sync_products:
            return 0
        
        import xmlrpc.client
        try:
            common = xmlrpc.client.ServerProxy(f'{self.remote_url}/xmlrpc/2/common')
            uid = common.authenticate(self.remote_database, self.remote_username, self.remote_password, {})
            
            if uid:
                models = xmlrpc.client.ServerProxy(f'{self.remote_url}/xmlrpc/2/object')
                product_count = models.execute_kw(
                    self.remote_database, uid, self.remote_password,
                    'product.product', 'search_count',
                    [[['type', '=', 'product']]]
                )
                return product_count
            return 0
        except Exception as e:
            return 0
    
    def _sync_products_from_remote(self):
        """Sincroniza productos desde la base de datos remota"""
        self.ensure_one()
        if not self.active or not self.sync_products:
            return
        
        import xmlrpc.client
        try:
            common = xmlrpc.client.ServerProxy(f'{self.remote_url}/xmlrpc/2/common')
            uid = common.authenticate(self.remote_database, self.remote_username, self.remote_password, {})
            
            if uid:
                models = xmlrpc.client.ServerProxy(f'{self.remote_url}/xmlrpc/2/object')
                
                # Obtener productos de la base remota
                remote_products = models.execute_kw(
                    self.remote_database, uid, self.remote_password,
                    'product.product', 'search_read',
                    [[['type', '=', 'product']]],
                    {'fields': ['name', 'default_code', 'list_price', 'standard_price', 'barcode', 'categ_id']}
                )
                
                # Sincronizar cada producto
                for remote_product in remote_products:
                    self._create_or_update_product(remote_product)
                    
        except Exception as e:
            # Manejo de errores
            pass
    
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
        import xmlrpc.client
        try:
            common = xmlrpc.client.ServerProxy(f'{self.remote_url}/xmlrpc/2/common')
            uid = common.authenticate(self.remote_database, self.remote_username, self.remote_password, {})
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

    def action_sync_pricelists_to_remote(self, execution_type='auto'):
        """Sincroniza las listas de precios marcadas hacia la base remota"""
        self.ensure_one()
        import xmlrpc.client
        
        log = self.env['sync.pictures.log'].create({
            'config_id': self.id,
            'status': 'in_progress',
            'execution_type': execution_type,
            'brand': 'LISTAS DE PRECIOS'
        })
        
        try:
            common = xmlrpc.client.ServerProxy(f'{self.remote_url}/xmlrpc/2/common')
            uid = common.authenticate(self.remote_database, self.remote_username, self.remote_password, {})
            if not uid:
                raise Exception('Autenticación fallida')
            
            models_rpc = xmlrpc.client.ServerProxy(f'{self.remote_url}/xmlrpc/2/object')
            
            pricelists = self.env['product.pricelist'].search([('sync_to_remote', '=', True)])
            synced_count = 0
            failed_count = 0
            line_vals = []
            
            for pl in pricelists:
                try:
                    # Buscar si existe en remoto por nombre
                    remote_pl_ids = models_rpc.execute_kw(
                        self.remote_database, uid, self.remote_password,
                        'product.pricelist', 'search', [[('name', '=', pl.name)]]
                    )
                    
                    # Preparar datos básicos (esto es simplificado, Odoo 13+ usa pricelist.item)
                    pl_data = {
                        'name': pl.name,
                        'currency_id': models_rpc.execute_kw(
                            self.remote_database, uid, self.remote_password,
                            'res.currency', 'search', [[('name', '=', pl.currency_id.name)]]
                        )[0] if pl.currency_id else False
                    }
                    
                    if remote_pl_ids:
                        models_rpc.execute_kw(self.remote_database, uid, self.remote_password, 'product.pricelist', 'write', [remote_pl_ids, pl_data])
                        comment = 'Actualizada correctamente'
                    else:
                        models_rpc.execute_kw(self.remote_database, uid, self.remote_password, 'product.pricelist', 'create', [pl_data])
                        comment = 'Creada correctamente'
                    
                    synced_count += 1
                    line_vals.append((0, 0, {
                        'pricelist_name': pl.name,
                        'status': 'synced',
                        'comment': comment
                    }))
                except Exception as e:
                    failed_count += 1
                    line_vals.append((0, 0, {
                        'pricelist_name': pl.name,
                        'status': 'failed',
                        'comment': str(e)
                    }))
            
            log.write({
                'status': 'completed',
                'pricelists_synced': synced_count,
                'pricelists_failed': failed_count,
                'pricelist_line_ids': line_vals
            })
        except Exception as e:
            log.write({
                'status': 'failed',
                'error_message': str(e)
            })

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