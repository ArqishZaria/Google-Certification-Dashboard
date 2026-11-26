from django import forms
from django.contrib import admin, messages
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError

from . import services
from .models import (Dismissal, SeatChange, Area, CourseEnrollment, DataImport, DataIssue, IdentityOverride, License, LocalCouncil, Participant,
                     Region, Registration, Specialization, SpecializationEnrollment, UserScope)


@admin.register(Specialization)
class SpecializationAdmin(admin.ModelAdmin):
    list_display = ("family", "name", "category", "reviewed", "slug")
    list_editable = ("category", "reviewed")
    list_filter = ("category", "reviewed")
    search_fields = ("name", "family", "slug")
    actions = ["recalc"]

    @admin.action(description="Recalculate all participants now (after changing categories)")
    def recalc(self, request, queryset):
        n = services.recompute_all()
        self.message_user(request, f"Recalculated {n} participants.", messages.SUCCESS)

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        self.message_user(request, "Category saved. Use the 'Recalculate' action (or the Data Quality page) to refresh the dashboard numbers.", messages.WARNING)


@admin.register(UserScope)
class UserScopeAdmin(admin.ModelAdmin):
    list_display = ("user", "role", "region", "local_council", "area")
    list_filter = ("role", "region")


@admin.register(IdentityOverride)
class IdentityOverrideAdmin(admin.ModelAdmin):
    list_display = ("email", "account", "cnic", "note", "created_at")
    search_fields = ("email", "cnic")


@admin.register(Participant)
class ParticipantAdmin(admin.ModelAdmin):
    list_display = ("name", "email", "region", "status", "pc_progress", "license_state")
    list_filter = ("region", "status", "license_state", "shortlisted")
    search_fields = ("name", "email", "cnic", "phone")
    readonly_fields = ("key", "public_id")


@admin.register(Registration)
class RegistrationAdmin(admin.ModelAdmin):
    list_display = ("form_id", "name", "region", "local_council", "gender")
    list_filter = ("region",)
    search_fields = ("name", "email", "cnic", "mobile")


@admin.register(License)
class LicenseAdmin(admin.ModelAdmin):
    list_display = ("email", "account", "state", "program_name", "match_method")
    list_filter = ("account", "state", "match_method")
    search_fields = ("email", "name")


admin.site.register([Region, LocalCouncil, Area, SpecializationEnrollment, CourseEnrollment, DataIssue, DataImport])


@admin.register(SeatChange)
class SeatChangeAdmin(admin.ModelAdmin):
    list_display = ("kind", "email", "new_value", "name", "region", "created_at")


@admin.register(Dismissal)
class DismissalAdmin(admin.ModelAdmin):
    list_display = ("code", "ref", "created_at")


class UserScopeForm(forms.ModelForm):
    class Meta:
        model = UserScope
        fields = ("role", "region", "local_council", "area")

    def clean(self):
        d = super().clean()
        role = d.get("role")
        if role and role not in UserScope.NATIONAL_ROLES and not (d.get("region") or d.get("local_council") or d.get("area")):
            raise ValidationError("Choose the Region (or Local council / Area) this user may see. Without one the user would see nothing.")
        return d


class UserScopeInline(admin.StackedInline):
    """Shown on the Add user / Change user page: what this person may see on the dashboard."""
    model = UserScope
    form = UserScopeForm
    fk_name = "user"
    extra = 1
    max_num = 1
    verbose_name = "Dashboard access"
    verbose_name_plural = "Dashboard access - choose the region this user may see (they will see nothing outside it)"


admin.site.unregister(User)


@admin.register(User)
class UserWithScopeAdmin(BaseUserAdmin):
    inlines = [UserScopeInline]
    list_display = ("username", "email", "is_staff", "dashboard_access")
    list_select_related = ("gcc_scope",)

    @admin.display(description="Dashboard access")
    def dashboard_access(self, obj):
        if obj.is_superuser:
            return "National (superuser)"
        s = getattr(obj, "gcc_scope", None)
        if s is None:
            return "none"
        return f"{s.get_role_display()} - " + (str(s.area) if s.area_id else s.local_council.name if s.local_council_id else s.region.name if s.region_id else "National")
