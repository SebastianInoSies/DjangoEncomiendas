from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static

admin.site.site_header = 'Sistema de Gestión de Encomiendas'
admin.site.site_title = 'Encomiendas Admin'
admin.site.index_title = 'Panel de Administración'

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', include('envios.urls')),
    path('accounts/', include('django.contrib.auth.urls')),
    path('api/<version>/', include('api.urls')),
]

if settings.DEBUG:
    from silk import urls as silk_urls
    urlpatterns += [path('silk/', include(silk_urls, namespace='silk'))]
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
