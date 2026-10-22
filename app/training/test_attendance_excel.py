from datetime import date, time
from decimal import Decimal
import io
from types import SimpleNamespace
from zipfile import ZipFile
import xml.etree.ElementTree as ET
from django.test import TestCase
from django.contrib.auth import get_user_model
from django.urls import reverse
from .attendance_excel import build_attendance_excel, TEMPLATE, NS
from .models import Course, Employee, Enrollment
from .planning import create_group


class AttendanceExcelTests(TestCase):
    def setUp(self):
        self.course=Course.objects.create(title='Liderança e Gestão')
        self.group=create_group(self.course,dict(dates=[date(2026,10,1)],trainer='Formador',provider='EPAL',location='Kikuxi',capacity=40,hours_per_day=Decimal('7'),break_hours=Decimal('1'),start_time=time(8),end_time=time(16)))
        self.user=get_user_model().objects.create_user('excel-reader')
        self.client.force_login(self.user)

    def people(self, count):
        return [SimpleNamespace(employee=SimpleNamespace(number=f'00{i:03}',name=f'Técnico fictício {i:02} António Manuel',source_data={'Função':'Chefe de Secção'},function='Chefe de Secção',category='Categoria',direction='Direcção de demonstração',area='',phone='923000000',email=f'tecnico{i}@example.org')) for i in range(count)]

    def inspect(self, data):
        archive=ZipFile(io.BytesIO(data));sheet=ET.fromstring(archive.read('xl/worksheets/sheet1.xml'))
        strings=ET.fromstring(archive.read('xl/sharedStrings.xml')).findall('s:si',NS)
        values={}
        for c in sheet.findall('.//s:sheetData/s:row/s:c',NS):
            if c.get('t')=='s':values[c.get('r')]=''.join(strings[int(c.find('s:v',NS).text)].itertext())
            else:values[c.get('r')]=''.join(c.itertext())
        return archive,sheet,values

    def test_page_boundaries_original_styles_and_blank_signatures(self):
        with ZipFile(TEMPLATE) as source:
            static={name:source.read(name) for name in ['xl/styles.xml','xl/theme/theme1.xml','xl/media/image1.png','xl/media/image2.png']}
            original=ET.fromstring(source.read('xl/worksheets/sheet1.xml'))
        for count,pages in [(1,1),(12,1),(13,2),(24,2),(25,3)]:
            archive,sheet,values=self.inspect(build_attendance_excel(self.group,date(2026,10,1),self.people(count)))
            for name,contents in static.items():self.assertEqual(archive.read(name),contents)
            for tag in ['cols','pageMargins','pageSetup','printOptions','headerFooter']:
                self.assertEqual(ET.tostring(sheet.find('s:'+tag,NS)),ET.tostring(original.find('s:'+tag,NS)))
            self.assertEqual(len(sheet.findall('s:sheetData/s:row',NS)),pages*24)
            self.assertEqual(len(sheet.findall('s:rowBreaks/s:brk',NS)),pages-1)
            self.assertEqual(len(sheet.findall('s:mergeCells/s:mergeCell',NS)),pages*10)
            drawing=ET.fromstring(archive.read('xl/drawings/drawing1.xml'))
            self.assertEqual(len(drawing),pages*2)
            for page in range(pages):
                self.assertEqual(values[f'J{page*24+23}'],f'Pág. {page+1}/{pages}')
                for slot in range(12):
                    row=page*24+slot+7
                    self.assertEqual(values[f'I{row}'],'');self.assertEqual(values[f'J{row}'],'')
            self.assertEqual(values['B7'],'00000')
            self.assertIn('923 000 000',values['F7'])
            self.assertIn('01/10/2026',values['A3'])
            self.assertIn('G1',values['E3'])

    def test_strings_are_literal_xml_safe_and_template_contains_no_source_contacts(self):
        people=self.people(1);people[0].employee.name='=1+1 & António <teste> \\1'
        archive,sheet,values=self.inspect(build_attendance_excel(self.group,date(2026,10,1),people))
        self.assertEqual(values['C7'],people[0].employee.name)
        self.assertFalse(sheet.findall('.//s:f',NS))
        binary=b' '.join(archive.read(n) for n in archive.namelist() if n.endswith('.xml'))
        for old in ['Brígida Noyra','923525085','não atendeu','BALUTA O1','Fabiana Patrícia']:
            self.assertNotIn(old.encode(),binary)

    def test_endpoint_download_validation_no_database_mutation_and_reader_access(self):
        url=reverse('attendance_excel',args=[self.group.pk]);self.assertEqual(self.client.get(url,{'date':'2026-10-01'}).status_code,400)
        employee=Employee.objects.create(number='00001',name='Técnico fictício António')
        Enrollment.objects.create(employee=employee,group=self.group)
        before=(self.group.revision,self.group.trainings.count(),Enrollment.objects.count())
        response=self.client.get(url,{'date':'2026-10-01'})
        self.assertEqual(response.status_code,200);self.assertIn('.xlsx',response['Content-Disposition'])
        self.assertTrue(b''.join(response.streaming_content).startswith(b'PK'))
        self.group.refresh_from_db();self.assertEqual(before,(self.group.revision,self.group.trainings.count(),Enrollment.objects.count()))
        self.assertContains(self.client.get(reverse('group_detail',args=[self.group.pk])),'Gerar lista em Excel')
        self.assertEqual(self.client.get(url,{'date':'2026-10-02'}).status_code,400)
        self.assertEqual(self.client.get(url,{'date':'invalid'}).status_code,400)
        self.group.state='Cancelada';self.group.save();self.assertEqual(self.client.get(url,{'date':'2026-10-01'}).status_code,400)
        self.client.logout();self.assertEqual(self.client.get(url,{'date':'2026-10-01'}).status_code,302)
