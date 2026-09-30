from django.contrib import admin

from .models import Company, Contact, DuplicateReview, Lead, Plant, Suppression


class PlantInline(admin.TabularInline):
    model = Plant
    extra = 0


class ContactInline(admin.TabularInline):
    model = Contact
    extra = 0


@admin.register(Company)
class CompanyAdmin(admin.ModelAdmin):
    list_display = ('company_name', 'industry', 'company_email', 'company_phone',
                    'data_quality_score', 'email_status', 'identity_status')
    list_filter = ('industry', 'email_status', 'identity_status')
    search_fields = ('company_name', 'normalized_name', 'company_email',
                     'company_phone', 'cin', 'gstin')
    readonly_fields = ('normalized_name', 'normalized_domain', 'normalized_email',
                       'normalized_phone', 'data_quality_score', 'source_history')
    inlines = [PlantInline, ContactInline]


@admin.register(Lead)
class LeadAdmin(admin.ModelAdmin):
    list_display = ('company', 'status', 'follow_up_date', 'profile_sent',
                    'lead_source', 'created_at')
    list_filter = ('status', 'lead_source', 'profile_sent')
    search_fields = ('company__company_name',)
    autocomplete_fields = ('company',)


@admin.register(DuplicateReview)
class DuplicateReviewAdmin(admin.ModelAdmin):
    list_display = ('existing_company', 'match_score', 'match_reason', 'status',
                    'created_at')
    list_filter = ('status', 'origin')


@admin.register(Suppression)
class SuppressionAdmin(admin.ModelAdmin):
    list_display = ('email', 'phone', 'reason', 'created_at')
    list_filter = ('reason',)
    search_fields = ('email', 'phone')


admin.site.register(Plant)
admin.site.register(Contact)
