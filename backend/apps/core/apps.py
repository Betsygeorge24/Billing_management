from django.apps import AppConfig


class CoreConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.core"

    def ready(self) -> None:
        from django.db.backends.signals import connection_created

        connection_created.connect(enable_sqlite_pragmas, dispatch_uid="core.sqlite_pragmas")


def enable_sqlite_pragmas(sender, connection, **kwargs) -> None:
    if connection.vendor != "sqlite":
        return
    with connection.cursor() as cursor:
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")