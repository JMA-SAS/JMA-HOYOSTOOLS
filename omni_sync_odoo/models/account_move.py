from odoo import models, fields, _, exceptions
import xmlrpc.client

class AccountMove(models.Model):
    _inherit = "account.move"

    def action_post(self):
        res = super().action_post()

        # Buscar TODAS las configuraciones activas con sincronización de compras habilitada (Multicliente)
        configs = self.env['omni.sync.config'].search([('active', '=', True), ('sync_purchases', '=', True)])
        if not configs:
            return res

        for config_rec in configs:
            self._sync_to_remote_purchase(config_rec)

        return res

    def _sync_to_remote_purchase(self, config_rec):
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

            # Validar si el modelo purchase.order existe en el remoto
            try:
                models_proxy.execute_kw(db, uid, password, 'purchase.order', 'search', [[]], {'limit': 1})
            except Exception:
                self.message_post(body=_("Error: El módulo de Compras (purchase.order) no está instalado en el servidor remoto."))
                return

            for move in self:
                if move.move_type == "out_invoice":
                    # Validar partner remoto
                    company_partner = move.company_id.partner_id
                    if not company_partner:
                        continue

                    remote_partner_ids = models_proxy.execute_kw(
                        db, uid, password, 'res.partner', 'search',
                        [[['vat', '=', company_partner.vat], ['name', '=', company_partner.name]]],
                        {'limit': 1}
                    )

                    if not remote_partner_ids:
                        move.message_post(body=_("No se encontró el partner de la compañía en el destino."))
                        continue

                    remote_partner_id = remote_partner_ids[0]

                    # Líneas
                    order_lines = []
                    for line in move.invoice_line_ids:
                        if not line.product_id or not line.product_id.default_code:
                            continue

                        remote_prod_ids = models_proxy.execute_kw(
                            db, uid, password, 'product.product', 'search',
                            [[("default_code", "=", line.product_id.default_code)]],
                            {"limit": 1}
                        )

                        if remote_prod_ids:
                            order_lines.append((0, 0, {
                                "name": line.name,
                                "product_id": remote_prod_ids[0],
                                "product_qty": line.quantity,
                                "price_unit": line.price_unit,
                                "date_planned": fields.Datetime.now(),
                            }))

                    if not order_lines:
                        continue

                    # Campaña
                    remote_campaign_id = False
                    if move.campaign_id:
                        campaign_ids = models_proxy.execute_kw(
                            db, uid, password, 'utm.campaign', 'search',
                            [[('name', '=', move.campaign_id.name)]], {'limit': 1}
                        )
                        if campaign_ids:
                            remote_campaign_id = campaign_ids[0]
                        else:
                            remote_campaign_id = models_proxy.execute_kw(
                                db, uid, password, 'utm.campaign', 'create', [{'name': move.campaign_id.name}]
                            )

                    # Crear OC
                    po_vals = {
                        "partner_id": remote_partner_id,
                        "partner_ref": move.name,
                        "order_line": order_lines,
                        "campaign_id": remote_campaign_id,
                    }

                    purchase_id = models_proxy.execute_kw(db, uid, password, "purchase.order", "create", [po_vals])

                    if config_rec.auto_confirm_po:
                        models_proxy.execute_kw(db, uid, password, "purchase.order", "button_confirm", [[purchase_id]])

                    move.message_post(body=_("Orden de Compra creada en remoto [%s] (ID: %s)") % (config_rec.name, purchase_id))

        except Exception as e:
            self.message_post(body=_("Error en sincronización remota con %s: %s") % (config_rec.name, str(e)))
