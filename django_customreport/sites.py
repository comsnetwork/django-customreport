from django.conf import settings
from django.utils.functional import update_wrapper
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.cache import never_cache
from django.shortcuts import render_to_response, redirect,get_object_or_404
from django.template import RequestContext
from django.core.urlresolvers import reverse
from django.contrib import messages

from django_relation_selector import views as rsviews

from django_customreport.helpers import process_queryset
from django_customreport import models as cm

class ReportSite(object):
	app_name = "None"
	name = "None"
	base_template = "customreport/base.html"
	nav_template = "customreport/nav.html"
	fields_template = "customreport/fields_form.html"
	admin_template = "customreport/admin_form.html"
	details_template = "customreport/report_form.html"
	columns_template = "customreport/columns_form.html"
	index_template = "customreport/index.html"

	def __init__(self):
		self.non_filter_fields = ['submit']
		self.fieldsets = getattr(self,'fieldsets',None)
		self.display_field_inclusions = getattr(self,'display_field_inclusions',None) or []
		self.display_field_exclusions = getattr(self,'display_field_exclusions',None) or []

		if not hasattr(self,'app_label'):
			self.app_label = self.queryset.model._meta.verbose_name

		self.name = self.app_label
		self._results = self.queryset.none()

	def get_context(self,request):
		return {'base_template': self.base_template}

	def report_view(self, view, cacheable=False):
		def inner(request, *args, **kwargs):
			return view(request, *args, **kwargs)
		if not cacheable:
			inner = never_cache(inner)
		# We add csrf_protect here so this function can be used as a utility
		# function for any view, without having to repeat 'csrf_protect'.
		if not getattr(view, 'csrf_exempt', False):
			inner = csrf_protect(inner)
		return update_wrapper(inner, view)

	def wrap(self,view, cacheable=False):
		def wrapper(*args, **kwargs):
			return self.report_view(view, cacheable)(*args, **kwargs)
		return update_wrapper(wrapper, view)

	def get_urls(self):
		from django.conf.urls.defaults import patterns, url, include

		# Admin-site-wide views.
		report_patterns = patterns('',
			url(r'^fields/$',
				self.wrap(self.fields, cacheable=True),
				name='fields'),
			url(r'^columns/$',
				self.wrap(self.columns, cacheable=True),
				name='columns'),
			url(r'^results/$',
				self.wrap(self.results, cacheable=True),
				name='results'),
			url(r'^save/$',
				self.wrap(self.save, cacheable=True),
				name='save'),
		)

		storedreport_patterns = patterns('',
			url(r'^recall/$',
				self.wrap(self.recall, cacheable=True),
				name='recall'),
			url(r'^details/$',
				self.wrap(self.details, cacheable=True),
				name='details'),
			url(r'^delete/$',
				self.wrap(self.delete),
				name='delete'),

			url(r'',include(report_patterns)),
		)

		urlpatterns = report_patterns + patterns('',
			url(r'^$',
				self.wrap(self.index),
				name='index'),
			url(r'^admin/$',
				self.wrap(self.admin),
				name='admin'),
			url(r'^relation/select/$',
				self.wrap(rsviews.relation_select),
				name='relation_select'),
			url(r'^column/remove/(?P<relation>.+)/$',
				self.wrap(self.remove_column),
				name='remove_column'),
			url(r'^reset/$',
				self.wrap(self.reset, cacheable=True),
				name='reset'),
			url(r'^(?P<report_id>[^/]+)/',include(storedreport_patterns)),
		)

		return urlpatterns

	def urls(self):
		return self.get_urls(), "%s-report" % self.app_label, self.app_label
	urls = property(urls)

	"""
	Hooks
	"""

	def get_queryset(self,request):
		return self.queryset

	def get_columns_form(self,request):
		from django_customreport.forms import ColumnForm
		return ColumnForm(self.app_label,self.get_queryset(request),request,data=request.GET or None,
				filter_fields=request.session.get('%s-report:filter_criteria' % self.app_label))

	def get_results(self,request,queryset,display_fields=None):
		filter = self.filterset_class(request.session.get('%s-report:filter_criteria_GET' % self.app_label),queryset=queryset)
		self._results = process_queryset(filter.qs,display_fields=display_fields)
		return self._results

	def get_report_form(self,request):
		from django_customreport.forms import ReportForm
		return ReportForm

	def reset_session(self,request):
		for i in ['filter_criteria','filter_GET','columns']:
			if request.session.get('%s-report:%s' % (self.app_label,i)):
				del request.session['%s-report:%s' % (self.app_label,i)]

	"""
	Views
	"""

	def reset(self,request):
		self.reset_session(request)
		return redirect("%s-report:fields" % self.app_label)

	def admin(self,request):
		instance, created = cm.ReportSite.objects.get_or_create(site_label=self.app_label)
		from django_customreport.forms import ReportSiteForm, ReportColumnForm
		form = ReportSiteForm(self,request.POST or None)
		column_forms = [ReportColumnForm(instance,request.POST or None,instance=i,prefix=i.pk) \
			for i in instance.reportcolumn_set.order_by('-relation')]

		if request.POST:
			selected_columns = []
			for k,v in request.POST.items():
				if v == 'on':
					col = k
					if '+' in k:
						col = k.split('+')[1]
					if '-' in col:
						continue
					selected_columns.append(col)

			for c in selected_columns:
				human_name = "consumer :: %s" % c.replace('__', ' :: ')
				cm.ReportColumn.objects.get_or_create(report_site=instance,relation=c,defaults={'human_name': human_name})

		if request.POST and all(f.is_valid() for f in column_forms):
			[f.save() for f in column_forms]
			messages.success(request,"Report information has been saved")
			return redirect("%s-report:admin" % self.app_label)
		context = {'form': form, 'column_forms': column_forms}
		context.update(self.get_context(request) or {})
		return render_to_response(self.admin_template, context, \
			context_instance=RequestContext(request))

	def remove_column(self,request,relation):
		cm.ReportColumn.objects.filter(relation=relation).delete()
		messages.success(request,"Column '%s' removed" % relation)
		return redirect("%s-report:admin" % self.app_label)

	def details(self,request,report_id):
		report = get_object_or_404(cm.Report,pk=report_id)

		form_class = self.get_report_form(request)

		form = form_class(request.POST or None,instance=report)

		if request.POST and form.is_valid():
			form.save()

			messages.success(request,"Report has been saved")

			return redirect("%s-report:index" % self.app_label)

		return render_to_response(self.details_template,{'form': form, 'nav_template':self.nav_template },context_instance=RequestContext(request))

	def save(self,request,report_id=None):
		data = {}
		for i in ['filter_criteria','filter_GET','columns']:
			data[i] = request.session.get("%s-report:%s" % (self.app_label,i))

		if report_id and not request.GET.get("as_new"):
			report = get_object_or_404(cm.Report,app_label=self.name,pk=report_id)
			report.data = data
			report.save()

		else:
			report = cm.Report.objects.create(app_label=self.app_label,data=data,added_by=request.user)

		messages.success(request,"Your report has been saved")

		return redirect(request.GET.get('return_url') or reverse("%s-report:details" % self.app_label,args=[report.pk]))

	def recall(self,request,report_id):
		report = get_object_or_404(cm.Report,app_label=self.name,pk=report_id)

		for k, v in report.data.iteritems():
			report_prefix = '%s-report' % self.app_label
			request.session[':'.join([report_prefix,k])] = v

		return redirect("%s-report:results" % self.app_label)

	def fields(self,request,report_id=None):
		filter = self.filterset_class(request.GET or None,queryset=self.get_queryset(request))

		form = filter.form

		form.initial.update(request.session.get('%s-report:filter_criteria' % self.app_label) or {})

		if request.GET and form.is_valid():
			request.session['%s-report:filter_criteria' % self.app_label] = form.cleaned_data
			request.session['%s-report:filter_GET' % self.app_label] = request.GET
			return redirect(reverse("%s-report:results" % self.app_label))

		fieldsets = []

		if not self.fieldsets:
			fieldsets.append((None,{'fields': [f for f in form] }))

		else:
			accounted_fields = []

			for fieldset in self.fieldsets:
				fields = []
				for field_name in fieldset[1]['fields']:
					for field in form:
						if field.name == field_name:
							fields.append(field)

							accounted_fields.append(field_name)
							break

				fieldsets.append((fieldset[0],{'fields': fields}))

			for name, field in form.fields.iteritems():
				if not name in accounted_fields:
					raise ValueError("Unaccounted field %s in fieldset" % name)
		return render_to_response(self.fields_template, {"form": form, "fieldsets": fieldsets, "nav_template": self.nav_template}, context_instance=RequestContext(request))

	def delete(self,request,report_id=None):
		report = get_object_or_404(cm.Report,app_label=self.name,pk=report_id)
		if report.added_by == request.user:
			name = report.name
			report.delete()
			messages.success(request,"Your report, \"%s\" has been deleted." % name )

		else:
			messages.error(request,"You do not have permission to delete that report.")

		return redirect("%s-report:index" % self.app_label)

	def columns(self,request,report_id=None):
		form = self.get_columns_form(request)
		form.initial.update({"display_fields": request.session.get("%s-report:columns" % self.app_label)})
		if request.GET and form.is_valid():
			request.session['%s-report:columns' % self.app_label] = form.cleaned_data.get('display_fields')
			return redirect(reverse("%s-report:results" % self.app_label))
		return render_to_response(self.columns_template,{'form': form, 'nav_template': self.nav_template},context_instance=RequestContext(request))

	def results(self,request,report_id=None):
		filter = self.filterset_class(request.session.get('%s-report:filter_GET' % self.app_label),queryset=self.get_queryset(request))
		columns = request.session.get('%s-report:columns' % self.app_label) or []
		queryset = self.get_results(request,filter.qs,display_fields=columns)
		for c in columns[:]:
			method_col = getattr(queryset.model, c, None)
			columns.remove(c)
			if callable(method_col):
				col_func = lambda o,c=c: getattr(o,c)()
				col_func.short_description = getattr(getattr(queryset.model,c),"short_description","")
				col_func.admin_order_field = getattr(getattr(queryset.model,c),"admin_order_field","")
				columns.append(col_func)
			else:
				col_func = lambda o,c=c: getattr(o,c)
				col_func.short_description = c
				col_func.admin_order_field = c
				columns.append(col_func)

		self.displayset_class.list_display = columns

		from django_displayset import views as displayset_views
		context = {'nav_template': self.nav_template}
		context.update(self.get_context(request) or {})
		return displayset_views.filterset_generic(request,filter,self.displayset_class,\
				queryset=queryset,extra_context=context)

	def index(self,request):
		saved_reports = cm.Report.objects.filter(added_by=request.user)
		old_report_session = False
		if request.session.get('%s-report:filter_criteria' % self.app_label, None):

			old_report_session = True
		context = {'saved_reports': saved_reports, 'old_report_session': old_report_session, 'nav_template': self.nav_template}
		return render_to_response(self.index_template, context, context_instance=RequestContext(request))
