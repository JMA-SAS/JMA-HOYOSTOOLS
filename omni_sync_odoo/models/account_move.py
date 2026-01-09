from odoo import models, fields, api, _
import xmlrpc.client
import logging

_logger = logging.getLogger(__name__)


class AccountMove(models.Model):
    _inherit = "account.move"

    is_synced = fields.Boolean(
        string="Sincronizado", 
        readonly=True, 
        copy=False,
        help="Indica si esta factura ha disparado la creación de una orden de compra en la instancia remota."
    )
    sync_log = fields.Html(
        string="Resumen de Sincronización", 
        readonly=True, 
        copy=False,
        help="Registro visual del proceso de creación de la orden de compra remota."
    )
    remote_order_ref = fields.Char(
        string="Referencia Remota", 
        readonly=True, 
        copy=False,
        help="Referencia de la Orden de Compra creada en el servidor remoto."
    )
    is_remote_order = fields.Boolean(
        string="Es Pedido Remoto", 
        default=False, 
        help="Identificador técnico para facturas que provienen de procesos de sincronización remota."
    )

    def action_post(self):
        """Extensión de la validación de factura para sincronizar compras."""
        res = super().action_post()

        # Buscar todas las configuraciones activas con sync de compras habilitado
        configs = self.env['omni.sync.config'].search([
            ('active', '=', True),
            ('sync_purchases', '=', True)
        ])

        if not configs:
            return res

        for config_rec in configs:
            self._sync_to_remote_purchase(config_rec)

        return res

    def _sync_to_remote_purchase(self, config_rec):
        """Lógica interna para conectar con el remoto y crear la OC."""
        url = config_rec.remote_url
        db = config_rec.remote_database
        username = config_rec.remote_username
        password = config_rec.remote_password

        try:
            common = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/common")
            uid = common.authenticate(db, username, password, {})
            if not uid:
                return

            models_proxy = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/object")

            # Validar módulo de compras en remoto
            try:
                models_proxy.execute_kw(
                    db, uid, password,
                    'purchase.order', 'search',
                    [[]], {'limit': 1}
                )
            except Exception:
                self.message_post(
                    body=_("Error: El módulo de Compras no está instalado en el servidor remoto.")
                )
                return

            for move in self:
                if move.move_type != "out_invoice" or move.is_synced or move.is_remote_order:
                    continue

                vendor_partner = move.partner_id
                if not vendor_partner:
                    continue

                # ------------------------------
                # Partner remoto (buscar / crear)
                # ------------------------------
                domain = []
                if vendor_partner.vat:
                    domain = [('vat', '=', vendor_partner.vat)]
                else:
                    domain = [('name', '=', vendor_partner.name)]

                remote_partner_ids = models_proxy.execute_kw(
                    db, uid, password,
                    'res.partner', 'search',
                    [domain],
                    {'limit': 1}
                )

                if not remote_partner_ids:
                    _logger.info(
                        "Proveedor no encontrado en remoto, creando: %s", vendor_partner.name
                    )

                    remote_partner_id = models_proxy.execute_kw(
                        db, uid, password,
                        'res.partner', 'create',
                        [{
                            'name': vendor_partner.name,
                            'vat': vendor_partner.vat,
                            'email': vendor_partner.email,
                            'phone': vendor_partner.phone,
                            'supplier_rank': 1,
                            'company_type': 'company',
                        }]
                    )
                else:
                    remote_partner_id = remote_partner_ids[0]

                # ------------------------------
                # Líneas de la OC
                # ------------------------------
                order_lines = []
                for line in move.invoice_line_ids:
                    if not line.product_id or not line.product_id.default_code:
                        continue

                    remote_prod_ids = models_proxy.execute_kw(
                        db, uid, password,
                        'product.product', 'search',
                        [[('default_code', '=', line.product_id.default_code)]],
                        {'limit': 1}
                    )

                    if not remote_prod_ids:
                        continue

                    order_lines.append((0, 0, {
                        'name': line.name,
                        'product_id': remote_prod_ids[0],
                        'product_qty': line.quantity,
                        'price_unit': line.price_unit,
                        'date_planned': fields.Datetime.now(),
                    }))

                if not order_lines:
                    move.message_post(
                        body=_("No se encontraron productos válidos para crear la Orden de Compra.")
                    )
                    continue

                # ------------------------------
                # Crear Orden de Compra
                # ------------------------------
                # Validar campos existentes en el remoto antes de enviarlos
                remote_fields = models_proxy.execute_kw(db, uid, password, 'purchase.order', 'fields_get', [[]], {'attributes': ['string']})
                
                # Obtener la URL base de esta instancia para enviarla como origen
                base_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url')
                origin_info = f"{base_url} ({self.env.cr.dbname})"
                
                po_vals = {
                    'partner_id': remote_partner_id,
                    'partner_ref': move.name,
                    'order_line': order_lines,
                }

                if 'is_synced' in remote_fields:
                    po_vals['is_synced'] = True
                if 'sync_connection_name' in remote_fields:
                    po_vals['sync_connection_name'] = origin_info

                purchase_id = models_proxy.execute_kw(
                    db, uid, password,
                    'purchase.order', 'create',
                    [po_vals]
                )

                # Confirmar automáticamente
                if config_rec.auto_confirm_po:
                    models_proxy.execute_kw(
                        db, uid, password,
                        'purchase.order', 'button_confirm',
                        [[purchase_id]]
                    )

                # Obtener el nombre de la OC remota
                remote_po_name = models_proxy.execute_kw(db, uid, password, 'purchase.order', 'read', [[purchase_id]], {'fields': ['name']})
                remote_ref = remote_po_name[0].get('name') if remote_po_name else str(purchase_id)

                log_html = f"""
                    <div style="width: 100%; margin-top: 10px; font-family: sans-serif;">
                        <table style="width: 100%; border-collapse: separate; border-spacing: 10px; table-layout: fixed;">
                            <tr>
                                <td style="width: 25%; background: #f8f9fa; padding: 15px; border-radius: 8px; border-left: 5px solid #007bff; box-shadow: 0 2px 4px rgba(0,0,0,0.05);">
                                    <div style="font-size: 11px; color: #666; text-uppercase; font-weight: bold; margin-bottom: 5px;">Referencia Remota</div>
                                    <div style="font-size: 16px; color: #007bff; font-weight: bold;">{remote_ref}</div>
                                </td>
                                <td style="width: 25%; background: #f8f9fa; padding: 15px; border-radius: 8px; border-left: 5px solid #28a745; box-shadow: 0 2px 4px rgba(0,0,0,0.05);">
                                    <div style="font-size: 11px; color: #666; text-uppercase; font-weight: bold; margin-bottom: 5px;">ID Remoto</div>
                                    <div style="font-size: 16px; color: #28a745; font-weight: bold;">{purchase_id}</div>
                                </td>
                                <td style="width: 25%; background: #f8f9fa; padding: 15px; border-radius: 8px; border-left: 5px solid #ffc107; box-shadow: 0 2px 4px rgba(0,0,0,0.05);">
                                    <div style="font-size: 11px; color: #666; text-uppercase; font-weight: bold; margin-bottom: 5px;">Conexión</div>
                                    <div style="font-size: 14px; color: #333; font-weight: bold; word-break: break-all;">{config_rec.name}</div>
                                </td>
                                <td style="width: 25%; background: #f8f9fa; padding: 15px; border-radius: 8px; border-left: 5px solid #17a2b8; box-shadow: 0 2px 4px rgba(0,0,0,0.05);">
                                    <div style="font-size: 11px; color: #666; text-uppercase; font-weight: bold; margin-bottom: 5px;">Líneas Sinc.</div>
                                    <div style="font-size: 16px; color: #17a2b8; font-weight: bold;">{len(order_lines)}</div>
                                </td>
                            </tr>
                        </table>
                        <div style="text-align: right; padding: 10px; font-size: 11px; color: #999; font-style: italic;">
                            Sincronizado el: {fields.Datetime.now()}
                        </div>
                    </div>
                """
                move.write({
                    'is_synced': True,
                    'remote_order_ref': remote_ref,
                    'sync_log': log_html
                })

                move.message_post(
                    body=_(
                        "Orden de Compra creada en remoto [%s] (Referencia: %s)"
                    ) % (config_rec.name, remote_ref)
                )

        except Exception as e:
            _logger.exception("Error en sincronización de compras")
            self.message_post(
                body=_("Error en sincronización remota con %s: %s")
                % (config_rec.name, str(e))
            )

class PurchaseOrder(models.Model):
    _inherit = "purchase.order"

    is_synced = fields.Boolean(
        string="Sincronizado", 
        readonly=True, 
        copy=False,
        help="Indica si esta orden de compra fue generada automáticamente desde una factura sincronizada."
    )
    sync_connection_name = fields.Char(
        string="Conexión Sincronizada", 
        readonly=True, 
        copy=False,
        help="Nombre de la base de datos o URL de origen que envió los datos para crear esta orden de compra."
    )

    # Campos para el dashboard
    @api.model
    def get_sync_stats(self):
        """Devuelve estadísticas de sincronización para el dashboard"""
        synced_orders = self.search([('is_synced', '=', True)])
        total_count = len(synced_orders)
        avg_value = sum(synced_orders.mapped('amount_total')) / total_count if total_count > 0 else 0.0
        return {
            'total_count': total_count,
            'avg_value': avg_value,
        }
