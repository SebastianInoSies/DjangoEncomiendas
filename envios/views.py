import datetime
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required, permission_required
from django.views.decorators.http import require_GET, require_POST, require_http_methods
from django.contrib import messages
from django.core.paginator import Paginator
from django.http import JsonResponse
from django.core.exceptions import PermissionDenied
from django.db.models import Q
from django.utils import timezone

from .models import Encomienda, Empleado, HistorialEstado
from .forms import EncomiendaForm
from config.choices import EstadoEnvio


def _obtener_o_crear_empleado(user):
    empleado = Empleado.objects.filter(email=user.email).first()
    if not empleado and user.is_staff:
        nombres = user.first_name or user.username
        apellidos = user.last_name or user.username
        empleado = Empleado.objects.create(
            codigo=f'EMP-{user.id:04d}',
            nombres=nombres,
            apellidos=apellidos,
            cargo='Administrador',
            email=user.email,
            fecha_ingreso=timezone.now().date(),
        )
    return empleado


def _generar_codigo_encomienda():
    year = timezone.now().strftime('%Y')
    prefix = f'ENC-{year}-'
    ultimo = Encomienda.objects.filter(
        codigo__startswith=prefix
    ).order_by('codigo').last()
    if ultimo:
        num = int(ultimo.codigo.split('-')[-1]) + 1
    else:
        num = 1
    return f'{prefix}{num:03d}'

@login_required
def dashboard(request):
    hoy = timezone.now().date()
    stats = [
        ('Activas', Encomienda.objects.activas().count(), 'primary', 'shipping-fast'),
        ('En tránsito', Encomienda.objects.en_transito().count(), 'info', 'truck'),
        ('Con retraso', Encomienda.objects.con_retraso().count(), 'danger', 'exclamation-triangle'),
        ('Entregadas hoy', Encomienda.objects.filter(
            estado=EstadoEnvio.ENTREGADO, fecha_entrega_real=hoy
        ).count(), 'success', 'check-circle'),
    ]
    context = {
        'stats': stats,
        'ultimas': Encomienda.objects.con_relaciones()[:5],
    }
    return render(request, 'envios/dashboard.html', context)

@login_required
@require_GET
def encomienda_lista(request):
    qs = Encomienda.objects.con_relaciones()
    estado = request.GET.get('estado', '')
    q = request.GET.get('q', '')

    if estado:
        qs = qs.filter(estado=estado)
    if q:
        qs = qs.filter(
            Q(codigo__icontains=q) |
            Q(remitente__apellidos__icontains=q) |
            Q(destinatario__apellidos__icontains=q)
        )

    paginator = Paginator(qs, 15)
    page_number = request.GET.get('page', 1)
    encomiendas = paginator.get_page(page_number)

    context = {
        'encomiendas': encomiendas,
        'estados': EstadoEnvio.choices,
        'estado_activo': estado,
        'q': q,
    }
    return render(request, 'envios/lista.html', context)

@login_required
@require_GET
def encomienda_detalle(request, pk):
    enc = get_object_or_404(Encomienda.objects.con_relaciones(), pk=pk)
    historial = enc.historial.select_related('empleado').all()
    context = {
        'encomienda': enc,
        'historial': historial,
        'estados': EstadoEnvio.choices,
    }
    return render(request, 'envios/detalle.html', context)

@login_required
@require_http_methods(['GET', 'POST'])
def encomienda_crear(request):
    empleado = _obtener_o_crear_empleado(request.user)
    if not empleado:
        messages.error(request, 'No tienes un perfil de empleado asociado.')
        return render(request, 'envios/form.html', {'form': EncomiendaForm(), 'titulo': 'Nueva Encomienda'})

    codigo_generado = _generar_codigo_encomienda()

    if request.method == 'POST':
        form = EncomiendaForm(request.POST)
        if form.is_valid():
            enc = form.save(commit=False)
            enc.codigo = codigo_generado
            enc.empleado_registro = empleado
            enc.save()
            messages.success(request, f'Encomienda {enc.codigo} registrada correctamente.')
            return redirect('encomienda_detalle', pk=enc.pk)
        messages.error(request, 'Corrige los errores del formulario.')
    else:
        form = EncomiendaForm(initial={'codigo': codigo_generado})

    return render(request, 'envios/form.html', {
        'form': form,
        'titulo': 'Nueva Encomienda',
        'codigo_generado': codigo_generado,
    })

@login_required
@require_POST
def encomienda_cambiar_estado(request, pk):
    enc = get_object_or_404(Encomienda, pk=pk)
    if enc.esta_entregada:
        raise PermissionDenied('No se puede cambiar el estado de una encomienda entregada.')

    nuevo_estado = request.POST.get('estado')
    observacion = request.POST.get('observacion', '')
    empleado = _obtener_o_crear_empleado(request.user)
    if not empleado:
        messages.error(request, 'No tienes un perfil de empleado asociado.')
        return redirect('encomienda_detalle', pk=pk)

    try:
        enc.cambiar_estado(nuevo_estado, empleado, observacion)
        messages.success(request, f'Estado actualizado: {enc.get_estado_display()}')
    except ValueError as e:
        messages.error(request, str(e))

    return redirect('encomienda_detalle', pk=pk)

@login_required
def encomienda_estado_json(request, pk):
    enc = get_object_or_404(Encomienda, pk=pk)
    return JsonResponse({
        'codigo': enc.codigo,
        'estado': enc.estado,
        'display': enc.get_estado_display(),
        'retraso': enc.tiene_retraso,
        'dias': enc.dias_en_transito,
    })
