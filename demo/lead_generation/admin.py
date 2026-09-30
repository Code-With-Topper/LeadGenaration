from django.contrib import admin
from .models import GenerationJob, Industry, SearchKeyword, DuplicateResolution

admin.site.register(GenerationJob)
admin.site.register(Industry)
admin.site.register(SearchKeyword)
admin.site.register(DuplicateResolution)
