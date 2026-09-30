from django.contrib import admin

from .models import Quotation, QuotationItem


class QuotationItemInline(admin.TabularInline):
    model = QuotationItem
    extra = 0


@admin.register(Quotation)
class QuotationAdmin(admin.ModelAdmin):
    list_display = ('quotation_number', 'company', 'total_amount', 'status', 'date')
    list_filter = ('status',)
    search_fields = ('quotation_number', 'company__company_name')
    inlines = [QuotationItemInline]
