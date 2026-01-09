from odoo import models, fields, api, _
from odoo.exceptions import ValidationError, UserError
import logging

_logger = logging.getLogger(__name__)

class ProductPricelist(models.Model):
    _inherit = 'product.pricelist'

    sync_to_remote = fields.Boolean(
        string='Sincronizar a Remoto', 
        default=False,
        help="Si se marca, esta lista de precios será elegible para ser sincronizada con la instancia remota configurada."
    )

class SyncConfig(models.Model):
    _name = 'omni.sync.config'
    _description = 'Parametrización de Conexiones B4B SYNC'
    _rec_name = 'name'

    name = fields.Char(
        string='Nombre de la Conexión', 
        required=True,
        help="Nombre descriptivo para identificar esta configuración de sincronización (ej. Conexión Principal, Sucursal Norte)."
    )
    
    # Configuración del servidor remoto (Origen de datos)
    remote_url = fields.Char(
        string='URL Servidor Origen (Remoto)', 
        required=True, 
        help="Dirección web completa de la instancia de Odoo remota. Debe incluir el protocolo (http:// o https://)."
    )
    remote_database = fields.Char(
        string='Base de Datos Origen', 
        required=True,
        help="Nombre técnico de la base de datos en el servidor remoto con la que se desea conectar."
    )
    remote_username = fields.Char(
        string='Usuario Origen', 
        required=True,
        help="Correo electrónico o nombre de usuario con permisos suficientes en la instancia remota."
    )
    remote_password = fields.Char(
        string='Contraseña Origen', 
        required=True,
        help="Contraseña o API Key del usuario en la instancia remota. Se recomienda usar llaves de API para mayor seguridad."
    )
    
    # Checkboxes de funcionalidad
    sync_products = fields.Boolean(
        string='Sincronizar Productos', 
        default=False,
        help="Habilita la importación de productos desde la instancia remota hacia esta base de datos local."
    )
    sync_images = fields.Boolean(
        string='Sincronizar Imágenes', 
        default=True,
        help="Habilita la descarga masiva de imágenes de productos desde el servidor remoto basándose en las marcas configuradas."
    )
    sync_pricelists = fields.Boolean(
        string='Sincronizar Listas de Precios', 
        default=False,
        help="Habilita la sincronización de las reglas de precios y listas marcadas para exportación remota."
    )
    sync_sales = fields.Boolean(
        string='Sincronizar Ventas', 
        default=False,
        help="Al confirmar un pedido de venta local, este se enviará automáticamente a la instancia remota."
    )
    sync_purchases = fields.Boolean(
        string='Sincronizar Compras (desde Facturas)', 
        default=False,
        help="Al validar una factura de cliente local, se creará automáticamente una orden de compra en la instancia remota."
    )
    
    # Configuración específica de imágenes
    brands_to_sync = fields.Text(
        string='Marcas a Sincronizar', 
        default='TOTAL', 
        help="Lista de marcas de productos a incluir en la sincronización de imágenes. Use 'TOTAL' para todas o separe nombres por comas."
    )
    batch_size = fields.Integer(
        string='Tamaño de Lote', 
        default=50,
        help="Cantidad de registros a procesar en cada iteración para evitar sobrecargar la memoria del servidor."
    )
    timeout = fields.Integer(
        string='Timeout (segundos)', 
        default=120,
        help="Tiempo máximo de espera para la respuesta del servidor remoto antes de cancelar la operación."
    )
    
    # Configuración específica de compras
    auto_confirm_po = fields.Boolean(
        string='Confirmar OC Automáticamente', 
        default=False,
        help="Si se activa, las órdenes de compra creadas en el remoto se confirmarán automáticamente pasando a estado 'Orden de Compra'."
    )
    
    active = fields.Boolean(
        string='Activo', 
        default=True,
        help="Permite archivar la configuración sin eliminarla. Solo las configuraciones activas ejecutarán procesos automáticos."
    )
    
    # Estadísticas para el tablero
    last_sync_date = fields.Datetime(
        string='Última Sincronización',
        help="Fecha y hora en la que se completó con éxito el último proceso de sincronización."
    )
    total_synced_products = fields.Integer(
        string='Productos Sincronizados', 
        default=0,
        readonly=True,
        help="Contador acumulado de productos que han sido procesados desde el origen."
    )
    total_synced_images = fields.Integer(
        string='Imágenes Sincronizadas', 
        default=0,
        readonly=True,
        help="Cantidad total de imágenes descargadas y vinculadas a productos locales."
    )
    total_synced_pricelists = fields.Integer(
        string='Listas de Precios Sincronizadas', 
        default=0,
        readonly=True,
        help="Número de listas de precios que han sido actualizadas en el remoto."
    )
    total_synced_sales = fields.Integer(
        string='Ventas Sincronizadas', 
        default=0,
        readonly=True,
        help="Total de pedidos de venta enviados exitosamente a la instancia remota."
    )
    total_synced_purchases = fields.Integer(
        string='Compras Sincronizadas', 
        default=0,
        readonly=True,
        help="Total de órdenes de compra generadas en el remoto a partir de facturas locales."
    )

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

    def _get_remote_connection(self):
        self.ensure_one()

        common, models = self._get_xmlrpc_proxies()

        uid = common.authenticate(
            self.remote_database,
            self.remote_username,
            self.remote_password,
            {}
        )

        if not uid:
            raise ValidationError('Autenticación fallida contra la base remota. Verifique URL, Base de Datos, Usuario y Contraseña.')

        return uid, models

    def update_stats(self):
        """Actualiza las estadísticas de forma manual o tras una sincronización para no ralentizar el tablero"""
        for record in self:
            # Imágenes y Precios desde logs
            log_data = self.env['sync.pictures.log'].search([('config_id', '=', record.id)])
            
            # Ventas sincronizadas hacia este remoto (usando el log o referencia)
            sales_count = self.env['sale.order'].search_count([
                ('is_synced', '=', True),
                '|',
                ('sync_log', 'like', f'>{record.remote_database}<'),
                ('sync_log', 'like', f'>{record.name}<')
            ])
            
            # Compras creadas desde facturas para este remoto
            purchases_count = self.env['account.move'].search_count([
                ('move_type', '=', 'out_invoice'),
                ('is_synced', '=', True),
                ('sync_log', 'like', f'>{record.name}<')
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

            return product_count

        except Exception as e:
            _logger.error(
                'Error obteniendo cantidad de productos remotos [%s]: %s',
                self.remote_url,
                str(e),
                exc_info=True
            )
            return 0

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
                    self.remote_database,
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

    @api.constrains('remote_url')
    def _check_urls(self):
        for record in self:
            if record.remote_url and not record.remote_url.startswith('http'):
                raise ValidationError('La URL debe comenzar con http:// o https://')

    def test_connection(self):
        """Prueba la conexión con el servidor remoto y devuelve una notificación al usuario."""
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
                        'title': _('Conexión Exitosa'),
                        'message': _('Se ha establecido conexión con la base de datos remota correctamente.'),
                        'type': 'success',
                        'sticky': False,
                    }
                }
            else:
                raise ValidationError(_('Autenticación fallida.'))
        except Exception as e:
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('Error de Conexión'),
                    'message': str(e),
                    'type': 'danger',
                    'sticky': True,
                }
            }

    def action_sync_images_now(self):
        """Inicia el proceso de sincronización de imágenes de forma manual."""
        self.ensure_one()
        wizard = self.env['sync.pictures.wizard'].create({
            'config_id': self.id,
            'brand_to_sync': self.brands_to_sync,
        })
        return wizard.action_sync_pictures()

    def action_sync_images_only(self):
        """Alias para el método llamado desde la vista si es necesario"""
        return self.action_sync_images_now()

    def action_manual_sync(self):
        """Sincroniza todo lo habilitado"""
        for record in self:
            if record.sync_products:
                record._sync_products_from_remote()
            if record.sync_images:
                record.action_sync_images_now()
            record.update_stats()
        return True

    def action_sync_products_to_remote(self):
        """Sincroniza productos desde el remoto"""
        self.ensure_one()
        res = self._sync_products_from_remote()
        self.update_stats()
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Sincronización de Productos'),
                'message': _('Creados: %s, Actualizados: %s') % (res['created'], res['updated']),
                'type': 'success',
            }
        }

    def action_sync_pricelists_to_remote(self):
        """Sincroniza listas de precios (Placeholder o implementación básica)"""
        self.ensure_one()
        # Aquí iría la lógica de listas de precios si se requiere
        return True
