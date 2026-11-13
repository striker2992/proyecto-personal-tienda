from django.contrib.auth import views as auth_views
from django.urls import path

from . import views

urlpatterns = [
    path('', views.home, name='home'),
    path('cuenta/registro/', views.register, name='register'),
    path('cuenta/ingresar/', views.login_view, name='login'),
    path('cuenta/salir/', auth_views.LogoutView.as_view(), name='logout'),
    path('cuenta/', views.account, name='account'),
    path('cuenta/compras/', views.my_purchases, name='my_purchases'),
    path(
        'cuenta/telefono/enviar-codigo/',
        views.phone_verification_send,
        name='phone_verification_send',
    ),
    path(
        'cuenta/telefono/verificar/',
        views.phone_verification_confirm,
        name='phone_verification_confirm',
    ),
    path('pedidos/crear/', views.order_create, name='order_create'),
    path('pedidos/<int:order_id>/', views.order_detail, name='order_detail'),
    path('pedidos/<int:order_id>/pagar/', views.payment_create, name='payment_create'),
    path('pago/retorno/<int:order_id>/', views.payment_return, name='payment_return'),
    path(
        'pagos/mercadopago/notificacion/',
        views.mercadopago_webhook,
        name='mercadopago_webhook',
    ),
    path('panel/productos/', views.product_create, name='product_create'),
    path(
        'panel/productos/<int:product_id>/editar/',
        views.product_edit,
        name='product_edit',
    ),
    path('panel/contacto/', views.contact_settings_update, name='contact_settings_update'),
    path('contacto/enviar/', views.submit_inquiry, name='submit_inquiry'),
    path('panel/consultas/<int:inquiry_id>/responder/', views.inquiry_reply, name='inquiry_reply'),
    path('panel/consultas/<int:inquiry_id>/eliminar/', views.inquiry_delete, name='inquiry_delete'),
    path('panel/ventas/', views.sales_dashboard, name='sales_dashboard'),
    path(
        'panel/pedidos/<int:order_id>/estado/',
        views.order_update_status,
        name='order_update_status',
    ),
    path(
        'panel/productos/<int:product_id>/visibilidad/',
        views.product_toggle,
        name='product_toggle',
    ),
]
