from django.urls import path, include
from rest_framework.routers import DefaultRouter
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView, SpectacularRedocView
from envios.viewsets import EncomiendaViewSet
from envios import api_views
from api.throttles import LoginRateThrottle

class ThrottledTokenObtainPairView(TokenObtainPairView):
    throttle_classes = [LoginRateThrottle]

router = DefaultRouter()
router.register('encomiendas', EncomiendaViewSet, basename='encomienda')

urlpatterns = [
    path('auth/token/', ThrottledTokenObtainPairView.as_view(), name='token_obtain'),
    path('auth/token/refresh/', TokenRefreshView.as_view(), name='token_refresh'),
    path('schema/', SpectacularAPIView.as_view(), name='schema'),
    path('docs/', SpectacularSwaggerView.as_view(url_name='schema'), name='swagger'),
    path('redoc/', SpectacularRedocView.as_view(url_name='schema'), name='redoc'),
    path('', include(router.urls)),
    path('clientes/', api_views.ClienteListView.as_view(), name='cliente-list'),
    path('rutas/', api_views.RutaListView.as_view(), name='ruta-list'),
]
