from django.db import migrations


PRODUCTS = [
    {
        'name': 'Container 20 FT Standard',
        'slug': 'container-20-ft-standard',
        'category': 'Uso general',
        'description': 'Ideal para almacenamiento y oficinas móviles.',
        'dimensions': '20 x 8 x 8.5 ft',
        'price': 2450000,
        'emoji': '📦',
    },
    {
        'name': 'Container 40 FT Standard',
        'slug': 'container-40-ft-standard',
        'category': 'Espacio amplio',
        'description': 'Perfecto para bodegas, talleres o módulos temporales.',
        'dimensions': '40 x 8 x 8.5 ft',
        'price': 3950000,
        'emoji': '📦',
    },
    {
        'name': 'Container 40 FT High Cube',
        'slug': 'container-40-ft-high-cube',
        'category': 'Altura extra',
        'description': 'Mayor altura para almacenamiento o vivienda modular.',
        'dimensions': '40 x 8 x 9.5 ft',
        'price': 4450000,
        'emoji': '📦',
    },
    {
        'name': 'Módulo oficina',
        'slug': 'modulo-oficina',
        'category': 'Oficina',
        'description': 'Módulo listo para uso con división interior y ventanas.',
        'dimensions': '20 FT + instalación',
        'price': 3200000,
        'emoji': '🏢',
    },
]


def seed_products(apps, schema_editor):
    Product = apps.get_model('store', 'Product')
    for product in PRODUCTS:
        Product.objects.get_or_create(slug=product['slug'], defaults=product)


class Migration(migrations.Migration):

    dependencies = [
        ('store', '0001_initial'),
    ]

    operations = [
        migrations.RunPython(seed_products, migrations.RunPython.noop),
    ]
