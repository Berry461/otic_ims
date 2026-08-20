from rest_framework.permissions import BasePermission, SAFE_METHODS


class IsAdmin(BasePermission):
    """Allows access only to admin (superuser or is_staff=True)."""
    def has_permission(self, request, view):
        return bool(
            request.user
            and request.user.is_authenticated
            and (request.user.is_staff or request.user.is_superuser)
        )


class IsAdminOrStaff(BasePermission):
    """Allows both admin and staff to access."""
    def has_permission(self, request, view):
        return bool(
            request.user
            and request.user.is_authenticated
            and (request.user.is_staff or request.user.is_superuser)
        )


class IsStaffOrReadOnly(BasePermission):
    """Allows staff to edit, but anyone authenticated can view."""
    def has_permission(self, request, view):
        if request.method in SAFE_METHODS:
            return bool(request.user and request.user.is_authenticated)

        return bool(
            request.user
            and request.user.is_authenticated
            and (request.user.is_staff or request.user.is_superuser)
        )


class IsOwnerOrAdmin(BasePermission):
    """Allows access to object owners or admin users."""
    def has_object_permission(self, request, view, obj):
        if request.user and (request.user.is_staff or request.user.is_superuser):
            return True
        return obj == request.user


class IsAuthenticatedUser(BasePermission):
    """Basic permission — just ensure the user is logged in."""
    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated)
