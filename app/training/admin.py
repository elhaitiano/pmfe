"""Bootstrap administration; business writes use the validated application workflows."""
from django.contrib.admin import AdminSite, ModelAdmin
from django.contrib.auth import get_user_model
from django.contrib.auth.admin import UserAdmin
from django.http import HttpResponseNotAllowed
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from .models import Employee, Course, Competency, RoleProfile, AuditEvent, Trainer


class PMFAdminSite(AdminSite):
    site_header = 'PMFE — Administração'
    site_title = 'Administração PMFE'
    index_title = 'Preparação e administração da plataforma'
    index_template = 'admin/pmf_index.html'
    site_url = '/'

    def has_permission(self, request):
        return request.user.is_active and request.user.is_staff and request.user.is_superuser

    def get_app_list(self, request, app_label=None):
        apps = super().get_app_list(request, app_label)
        labels = {'Employee':'Trabalhadores', 'Course':'Catálogo de formação',
                  'Competency':'Competências', 'RoleProfile':'Funções e regras RH', 'User':'Contas de utilizador'}
        for app in apps:
            app['name'] = 'Cadastros iniciais' if app['app_label'] == 'training' else 'Contas e acessos'
            for model in app['models']:
                model['name'] = labels.get(model['object_name'], model['name'])
        return apps

    def index(self, request, extra_context=None):
        steps = [
            ('Formadores', 'Gerir o cadastro e os contactos dos formadores.', reverse('trainer_index'), Trainer.objects.count()),
            ('1. Contas e acessos', 'Crie contas individuais. Defina o nível de acesso após criar a conta.',
             reverse('admin:auth_user_changelist'), get_user_model().objects.count()),
            ('2. Trabalhadores', 'Registe o cadastro antes das inscrições ou avaliações.',
             reverse('admin:training_employee_changelist'), Employee.objects.count()),
            ('3. Catálogo de formação', 'Crie as acções; depois prepare turmas e inscrições em Planeamento.',
             reverse('admin:training_course_changelist'), Course.objects.count()),
            ('4. Competências', 'Defina as competências que serão representadas no mapa de avaliação.',
             reverse('admin:training_competency_changelist'), Competency.objects.count()),
            ('5. Funções e regras RH', 'Associe competências às funções, com nível esperado, peso e validação RH.',
             reverse('admin:training_roleprofile_changelist'), RoleProfile.objects.count()),
        ]
        return super().index(request, {**(extra_context or {}), 'setup_steps':steps})


site = PMFAdminSite(name='admin')


class AccountAdmin(UserAdmin):
    fieldsets = (
        (None, {'fields':('username','password')}),
        ('Identificação', {'fields':('first_name','last_name','email')}),
        ('Acesso à plataforma', {'fields':('is_active','is_staff','is_superuser'),
            'description':'Conta normal: consulta e avaliações atribuídas. Membro da equipa: gestão operacional. Superutilizador: administração de contas e cópia integral. Não atribua privilégios administrativos a um avaliador apenas para responder a um ciclo.'}),
        ('Datas', {'fields':('last_login','date_joined')}),
    )
    readonly_fields = ('last_login','date_joined')
    list_display = ('username','first_name','last_name','is_active','is_staff','is_superuser')
    list_filter = ('is_active','is_staff','is_superuser')
    filter_horizontal = ()
    actions = None

    def get_readonly_fields(self, request, obj=None):
        fields = super().get_readonly_fields(request, obj)
        if obj and obj.pk == request.user.pk:
            return (*fields,'is_active','is_staff','is_superuser')
        return fields

    def has_delete_permission(self, request, obj=None):
        # Deactivate accounts: evaluator identities and historical authors must remain.
        return False

    def save_model(self, request, obj, form, change):
        super().save_model(request,obj,form,change)
        AuditEvent.objects.create(actor=request.user.username,action='ADMIN_ACCOUNT',
            detail=f'Conta #{obj.pk}; {"alterada" if change else "criada"}; activa={obj.is_active}; equipa={obj.is_staff}; superutilizador={obj.is_superuser}')


site.register(get_user_model(), AccountAdmin)


class WorkflowAdmin(ModelAdmin):
    """Searchable entry points, without a second writer bypassing versions/audit."""
    add_route = None
    change_route = None
    actions = None

    def has_delete_permission(self, request, obj=None):
        return False

    def add_view(self, request, form_url='', extra_context=None):
        if request.method != 'GET':
            return HttpResponseNotAllowed(['GET'])
        return redirect(self.add_route)

    def change_view(self, request, object_id, form_url='', extra_context=None):
        if request.method != 'GET':
            return HttpResponseNotAllowed(['GET'])
        obj = get_object_or_404(self.model, pk=object_id)
        return redirect(self.change_route, pk=obj.pk)


class EmployeeAdmin(WorkflowAdmin):
    add_route = 'employee_create'
    change_route = 'employee_edit'
    list_display = ('number','name','direction','function')
    search_fields = ('number','name','direction','function')
    list_filter = ('direction',)


class CourseAdmin(WorkflowAdmin):
    add_route = 'course_create'
    change_route = 'course_edit'
    list_display = ('code','title','validation_status','active')
    search_fields = ('code','title')
    list_filter = ('active','validation_status')


class CompetencyAdmin(WorkflowAdmin):
    add_route = 'competency_create'
    change_route = 'competency_edit'
    list_display = ('name','dimension','active')
    search_fields = ('name','dimension')
    list_filter = ('active',)


class RoleAdmin(WorkflowAdmin):
    add_route = 'role_create'
    change_route = 'role_edit'
    list_display = ('title','active')
    search_fields = ('title',)
    list_filter = ('active',)


site.register(Employee,EmployeeAdmin)
site.register(Course,CourseAdmin)
site.register(Competency,CompetencyAdmin)
site.register(RoleProfile,RoleAdmin)
