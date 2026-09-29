from django.conf import settings
from django.db import models


class AuditLog(models.Model):
	user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="audit_events")
	action = models.CharField(max_length=80, db_index=True)
	before_data = models.JSONField(default=dict, blank=True)
	after_data = models.JSONField(default=dict, blank=True)
	object_type = models.CharField(max_length=100, blank=True)
	object_id = models.CharField(max_length=64, blank=True)
	created_at = models.DateTimeField(auto_now_add=True, db_index=True)

	class Meta:
		ordering = ["-created_at"]
		indexes = [models.Index(fields=["action", "created_at"])]

	def __str__(self) -> str:
		return f"{self.action} at {self.created_at:%Y-%m-%d %H:%M}"