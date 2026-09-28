"""
Admin de usuarios.

Es la via oficial para dar de alta al personal de la clinica: aca se elige el
rol (recepcion / medico / admin) y se marca must_change_password para forzar
que el usuario ponga su propia contrasena en el primer login.
"""

from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin
from django.contrib.auth.forms import UserChangeForm, UserCreationForm
from django.utils.html import format_html

from .models import EmailVerificationCode, Role, User


class UserCreationFormForAdmin(UserCreationForm):
    class Meta:
        model = User
        fields = ("email", "nombre", "rol", "telefono")


class UserChangeFormForAdmin(UserChangeForm):
    class Meta:
        model = User
        fields = "__all__"


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    form = UserChangeFormForAdmin
    add_form = UserCreationFormForAdmin

    list_display = (
        "email",
        "nombre",
        "rol_badge",
        "email_verified",
        "is_active",
        "date_joined",
    )
    list_filter = ("rol", "is_active", "email_verified", "must_change_password")
    search_fields = ("email", "nombre", "telefono")
    ordering = ("-date_joined",)
    readonly_fields = ("id", "date_joined", "last_login")

    fieldsets = (
        (None, {"fields": ("id", "email", "password")}),
        ("Perfil", {"fields": ("nombre", "telefono", "rol")}),
        (
            "Acceso",
            {
                "fields": (
                    "is_active",
                    "email_verified",
                    "must_change_password",
                    "is_staff",
                    "is_superuser",
                    "groups",
                    "user_permissions",
                )
            },
        ),
        ("Fechas", {"fields": ("date_joined", "last_login")}),
    )

    add_fieldsets = (
        (
            None,
            {
                "classes": ("wide",),
                "fields": ("email", "nombre", "rol", "telefono", "password1", "password2"),
            },
        ),
    )

    actions = ("verify_email", "force_password_change", "activate", "deactivate")

    @admin.display(description="Rol", ordering="rol")
    def rol_badge(self, obj):
        colors = {
            Role.ADMIN: "#b91c1c",
            Role.MEDICO: "#1d4ed8",
            Role.RECEPCION: "#a16207",
            Role.PACIENTE: "#4b5563",
        }
        return format_html(
            '<span style="color:{};font-weight:600;">{}</span>',
            colors.get(obj.rol, "#111"),
            obj.get_rol_display(),
        )

    @admin.action(description="Marcar email como verificado")
    def verify_email(self, request, queryset):
        updated = queryset.update(email_verified=True)
        self.message_user(request, f"{updated} email(s) marcados como verificados.")

    @admin.action(description="Forzar cambio de contraseña en el próximo login")
    def force_password_change(self, request, queryset):
        updated = queryset.update(must_change_password=True)
        self.message_user(
            request, f"{updated} usuario(s) deben cambiar su contraseña."
        )

    @admin.action(description="Activar usuarios")
    def activate(self, request, queryset):
        updated = queryset.update(is_active=True)
        self.message_user(request, f"{updated} usuario(s) activados.")

    @admin.action(description="Desactivar usuarios")
    def deactivate(self, request, queryset):
        updated = queryset.update(is_active=False)
        self.message_user(request, f"{updated} usuario(s) desactivados.")


@admin.register(EmailVerificationCode)
class EmailVerificationCodeAdmin(admin.ModelAdmin):
    list_display = (
        "user",
        "purpose_display",
        "created_at",
        "expires_at",
        "is_usable_now",
    )
    list_filter = ("purpose",)
    search_fields = ("user__email",)
    ordering = ("-created_at",)
    # Solo lectura: el codigo en claro no existe en la base, y borrarlo es la
    # unica forma de invalidarlo.
    readonly_fields = ("user", "purpose", "code_hash", "created_at", "expires_at", "used_at", "attempts")

    def has_add_permission(self, request):
        return False

    @admin.display(description="Uso", boolean=True)
    def is_usable_now(self, obj):
        return obj.is_usable

    @admin.display(description="Propósito")
    def purpose_display(self, obj):
        return obj.get_purpose_display()
