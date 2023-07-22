# Django
from rest_framework import serializers

# Models
from apps.threads.models.thread import Thread


class ThreadSerializer(serializers.ModelSerializer):
    
    def to_representation(self, instance):
        representation = super().to_representation(instance)
        subs = Thread.objects.filter(sub=instance)
        representation["responses_count"] = subs.count()
        
        if not self.context.get("short"):
            representation["responses"] = ThreadSerializer(
                subs, many=True).data

        representation.pop("sub")
        representation.pop("create_at")
        representation.pop("update_at")
        return representation
    
    class Meta:
        model = Thread
        exclude = ('is_active',)
