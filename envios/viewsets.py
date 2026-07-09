from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from django.utils import timezone
from django.core.cache import cache
from django.utils.decorators import method_decorator
from django.views.decorators.cache import cache_page
from django.views.decorators.vary import vary_on_headers
from drf_spectacular.utils import (
    extend_schema, extend_schema_view, OpenApiParameter,
    OpenApiResponse, OpenApiExample, OpenApiTypes,
)
from config.settings import CACHE_TTL
from api.pagination import EncomiendaPagination, HistorialPagination
from api.throttles import EmpleadoRateThrottle, CambioEstadoThrottle
from api.exceptions import EstadoInvalidoError, EncomiendaYaEntregadaError
from .models import Encomienda, Empleado
from .serializers import (
    EncomiendaSerializer, EncomiendaListSerializer,
    EncomiendaDetailSerializer, EncomiendaV2Serializer,
    HistorialEstadoSerializer,
)

@extend_schema_view(
    list=extend_schema(summary='Listar encomiendas',
        description='Devuelve la lista paginada de encomiendas. Soporta filtros por estado, búsqueda y ordenamiento.',
        tags=['Encomiendas']),
    create=extend_schema(summary='Crear encomienda',
        description='Registra una nueva encomienda en el sistema.', tags=['Encomiendas']),
    retrieve=extend_schema(summary='Detalle de encomienda',
        description='Devuelve los datos completos de una encomienda con remitente, destinatario, ruta e historial.',
        tags=['Encomiendas']),
    update=extend_schema(summary='Actualizar encomienda', tags=['Encomiendas']),
    partial_update=extend_schema(summary='Actualizar parcial', tags=['Encomiendas']),
    destroy=extend_schema(summary='Eliminar encomienda', tags=['Encomiendas']),
)
class EncomiendaViewSet(viewsets.ModelViewSet):
    queryset = Encomienda.objects.con_relaciones()
    permission_classes = [IsAuthenticated]
    pagination_class = EncomiendaPagination
    throttle_classes = [EmpleadoRateThrottle]

    def get_serializer_class(self):
        version = getattr(self.request, 'version', 'v1')
        if version == 'v2':
            return EncomiendaV2Serializer
        if self.action == 'list':
            return EncomiendaListSerializer
        if self.action == 'retrieve':
            return EncomiendaDetailSerializer
        return EncomiendaSerializer

    def get_queryset(self):
        qs = Encomienda.objects.con_relaciones()
        if self.action == 'list':
            qs = qs.only('id', 'codigo', 'estado', 'peso_kg', 'costo_envio',
                         'fecha_registro', 'fecha_entrega_est',
                         'remitente__nombres', 'remitente__apellidos',
                         'destinatario__nombres', 'destinatario__apellidos',
                         'ruta__destino')
        estado = self.request.query_params.get('estado')
        if estado:
            qs = qs.filter(estado=estado)
        return qs

    def perform_create(self, serializer):
        empleado = Empleado.objects.filter(email=self.request.user.email).first()
        if not empleado:
            from datetime import date
            empleado = Empleado.objects.create(
                codigo=f'EMP-{self.request.user.id:04d}',
                nombres=self.request.user.first_name or self.request.user.username,
                apellidos=self.request.user.last_name or self.request.user.username,
                cargo='Administrador',
                email=self.request.user.email,
                fecha_ingreso=date.today(),
            )
        serializer.save(empleado_registro=empleado)

    def get_throttles(self):
        if self.action == 'cambiar_estado':
            return [CambioEstadoThrottle()]
        return super().get_throttles()

    def list(self, request, *args, **kwargs):
        response = super().list(request, *args, **kwargs)
        response['X-API-Version'] = getattr(request, 'version', 'v1')
        return response

    def retrieve(self, request, *args, **kwargs):
        response = super().retrieve(request, *args, **kwargs)
        response['X-API-Version'] = getattr(request, 'version', 'v1')
        return response

    @extend_schema(
        summary='Cambiar estado de encomienda',
        description='Cambia el estado de una encomienda y registra el cambio en el historial.',
        request=OpenApiTypes.OBJECT,
        responses={200: EncomiendaSerializer, 400: OpenApiResponse(description='Estado inválido')},
        examples=[
            OpenApiExample('Pasar a En tránsito',
                value={'estado': 'TR', 'observacion': 'Recogido en agencia Lima'},
                request_only=True),
            OpenApiExample('Marcar como Entregado',
                value={'estado': 'EN', 'observacion': 'Entregado al destinatario'},
                request_only=True),
        ],
        tags=['Encomiendas'],
    )
    @action(detail=True, methods=['post'], url_path='cambiar_estado')
    def cambiar_estado(self, request, pk=None):
        enc = self.get_object()
        if enc.esta_entregada:
            raise EncomiendaYaEntregadaError()
        nuevo_estado = request.data.get('estado')
        observacion = request.data.get('observacion', '')
        if not nuevo_estado:
            return Response({'error': 'El campo estado es requerido.'}, status=400)
        try:
            empleado = Empleado.objects.get(email=request.user.email)
            enc.cambiar_estado(nuevo_estado, empleado, observacion)
            cache.delete_many([
                f'estadisticas_empleado_{request.user.id}',
                f'encomienda_detalle_{pk}',
            ])
            return Response(EncomiendaSerializer(enc).data)
        except ValueError as e:
            raise EstadoInvalidoError(detail=str(e))

    @extend_schema(
        summary='Encomiendas con retraso',
        description='Lista todas las encomiendas activas cuya fecha estimada de entrega ya pasó.',
        tags=['Encomiendas'],
    )
    @action(detail=False, methods=['get'], url_path='con_retraso')
    def con_retraso(self, request):
        qs = Encomienda.objects.con_retraso().con_relaciones()
        return Response(self.get_serializer(qs, many=True).data)

    @extend_schema(
        summary='Encomiendas pendientes',
        description='Lista todas las encomiendas en estado Pendiente.',
        tags=['Encomiendas'],
    )
    @action(detail=False, methods=['get'])
    def pendientes(self, request):
        qs = Encomienda.objects.pendientes().con_relaciones()
        return Response(self.get_serializer(qs, many=True).data)

    @extend_schema(
        summary='Historial de estados',
        description='Devuelve el historial de cambios de estado paginado con limit/offset.',
        parameters=[
            OpenApiParameter('limit', type=int, description='Número de resultados', default=10),
            OpenApiParameter('offset', type=int, description='Posición de inicio', default=0),
        ],
        tags=['Encomiendas'],
    )
    @action(detail=True, methods=['get'], url_path='historial')
    def historial(self, request, pk=None):
        enc = self.get_object()
        qs = enc.historial.select_related('empleado').order_by('-fecha_cambio')
        paginator = HistorialPagination()
        page = paginator.paginate_queryset(qs, request)
        if page is not None:
            return paginator.get_paginated_response(
                HistorialEstadoSerializer(page, many=True).data)
        return Response(HistorialEstadoSerializer(qs, many=True).data)

    @extend_schema(
        summary='Estadísticas globales',
        description='Contadores del sistema: activas, en tránsito, con retraso y entregadas hoy.',
        tags=['Encomiendas'],
    )
    @action(detail=False, methods=['get'])
    def estadisticas(self, request):
        cache_key = f'estadisticas_empleado_{request.user.id}'
        data = cache.get(cache_key)
        if data is None:
            hoy = timezone.now().date()
            data = {
                'total_activas': Encomienda.objects.activas().count(),
                'en_transito': Encomienda.objects.en_transito().count(),
                'con_retraso': Encomienda.objects.con_retraso().count(),
                'entregadas_hoy': Encomienda.objects.filter(
                    estado='EN', fecha_entrega_real=hoy).count(),
            }
            cache.set(cache_key, data, CACHE_TTL)
        return Response(data)

    @extend_schema(
        summary='Crear múltiples encomiendas',
        description='Crea varias encomiendas en una sola petición.',
        tags=['Encomiendas'],
    )
    @action(detail=False, methods=['post'], url_path='bulk_create')
    def bulk_create(self, request):
        serializer = self.get_serializer(data=request.data, many=True)
        if not serializer.is_valid():
            return Response(serializer.errors, status=400)
        empleado = Empleado.objects.filter(email=request.user.email).first()
        if not empleado:
            from datetime import date
            empleado = Empleado.objects.create(
                codigo=f'EMP-{request.user.id:04d}',
                nombres=request.user.first_name or request.user.username,
                apellidos=request.user.last_name or request.user.username,
                cargo='Administrador',
                email=request.user.email,
                fecha_ingreso=date.today(),
            )
        encomiendas = serializer.save(empleado_registro=empleado)
        return Response(self.get_serializer(encomiendas, many=True).data, status=201)

    @extend_schema(
        summary='Cambiar estado a múltiples encomiendas',
        description='Cambia el estado de varias encomiendas. Reporta errores individuales.',
        tags=['Encomiendas'],
    )
    @action(detail=False, methods=['patch'], url_path='bulk_estado')
    def bulk_estado(self, request):
        ids = request.data.get('ids', [])
        nuevo_estado = request.data.get('estado')
        observacion = request.data.get('observacion', '')
        if not ids:
            return Response({'error': 'El campo ids es requerido.'}, status=400)
        if not nuevo_estado:
            return Response({'error': 'El campo estado es requerido.'}, status=400)
        try:
            empleado = Empleado.objects.get(email=request.user.email)
        except Empleado.DoesNotExist:
            return Response({'error': 'El usuario no tiene un empleado asociado.'}, status=403)
        encomiendas = Encomienda.objects.filter(id__in=ids)
        actualizadas = []
        errores = []
        for enc in encomiendas:
            try:
                enc.cambiar_estado(nuevo_estado, empleado, observacion)
                actualizadas.append(enc.id)
            except ValueError as e:
                errores.append({'id': enc.id, 'error': str(e)})
        ids_procesados = list(encomiendas.values_list('id', flat=True))
        no_encontrados = [i for i in ids if i not in ids_procesados]
        return Response({
            'actualizadas': actualizadas,
            'errores': errores,
            'no_encontrados': no_encontrados,
            'total': len(actualizadas),
        })
