from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from . import resolve, services
from .models import DataImport
from .views import base_ctx, is_staff_user


def _eff(request):
    return services.effective(request.user, {}) or {"label": "National", "level": "national", "q": Q(), "group_key": "region"}


def _allowed(request):
    return is_staff_user(request.user) and _eff(request)["level"] == "national"


@login_required
def resolve_page(request):
    if not _allowed(request):
        return render(request, "main/no_access.html", status=403)
    eff = _eff(request)
    imp = DataImport.objects.first()
    if imp is None:
        return render(request, "main/empty.html", base_ctx(request, eff))
    return render(request, "main/resolve.html", base_ctx(request, eff, **resolve.build_context(imp)))


@require_POST
@login_required
def resolve_act(request):
    if not _allowed(request):
        return render(request, "main/no_access.html", status=403)
    try:
        messages.success(request, resolve.perform(request.POST.get("action"), request.POST, request.user))
    except ValueError as e:
        messages.error(request, str(e))
    return redirect(request.POST.get("back") or "resolve")


@require_POST
@login_required
def resolve_accept_all(request):
    """One click: merge every confident suggestion, then recalculate."""
    if not _allowed(request):
        return render(request, "main/no_access.html", status=403)
    imp = DataImport.objects.first()
    if imp is None:
        return redirect("resolve")
    n = resolve.accept_all(imp, request.user)
    messages.success(request, f"{n} suggested match(es) accepted.")
    return resolve_apply(request)


@require_POST
@login_required
def resolve_apply(request):
    """Re-run the import from the stored files so every saved decision takes effect."""
    if not _allowed(request):
        return render(request, "main/no_access.html", status=403)
    try:
        imp = services.import_components(request.user, allow_fewer_accounts=True)
        messages.success(request, f"Numbers recalculated. Data as of {imp.as_of:%d %b %Y}: {imp.stats.get('participants_licensed', 0)} licensed participants.")
    except ValueError as e:
        messages.error(request, f"Could not recalculate: {e}. Upload the three files again on the Import page.")
    except Exception as e:
        messages.error(request, f"Recalculation failed and NOTHING was changed: {e}")
    return redirect("resolve")
