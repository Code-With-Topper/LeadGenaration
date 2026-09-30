from django.contrib import admin

from .models import City, District, GenerationJob, Industry, SearchKeyword


class SearchKeywordInline(admin.TabularInline):
    model = SearchKeyword
    extra = 1


@admin.register(Industry)
class IndustryAdmin(admin.ModelAdmin):
    list_display = ('name', 'is_active', 'sort_order')
    inlines = [SearchKeywordInline]


class CityInline(admin.TabularInline):
    model = City
    extra = 1


@admin.register(District)
class DistrictAdmin(admin.ModelAdmin):
    list_display = ('name', 'state', 'is_active')
    list_filter = ('state', 'is_active')
    inlines = [CityInline]


@admin.register(GenerationJob)
class GenerationJobAdmin(admin.ModelAdmin):
    list_display = ('id', 'industry', 'scope', 'status', 'leads_found',
                    'duplicate_leads', 'created_at')
    list_filter = ('status', 'state')
