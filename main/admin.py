from django.contrib import admin, messages

from . import services
from .models import (Area, CourseEnrollment, DataImport, DataIssue, IdentityOverride, License, LocalCouncil, Participant,
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
