import re
from warnings import warn
from app.forms import MissingFieldReportForm, MissingSubfieldReportForm, SelectAuthority
from dlx.marc import Bib, Auth, Matcher, OrMatch, BibSet, AuthSet, QueryDocument, Condition
from bson.regex import Regex
from natsort import natsorted
from collections import Counter

### Report base class. Not for use.

class Report(object):
    def __init__(self):
        self.form_class = SeletAuthority
        
        # these attributes must be implemented by the subclasses
        for att in ('name', 'title', 'description', 'category', 'form_class', 'expected_params', 'field_names'):
            if not hasattr(self, att):
                raise Exception('Required attribute "{}" is missing')
    
    def validate_args(self,args):
        for param in self.expected_params:
            if param not in args.keys():
                raise Exception('Expected param "{}" not found'.format(param))
            
    def execute(self,args):
        raise Exception('execute() must be implemented by the subclass')

### Missing field type

class MissingField(Report):
    def __init__(self,tag):
        self.name = '{}_missing_{}'.format(self.type, tag)
        self.title = 'Missing field - ' + tag
        #self.description = '{} records from the given body/session that don\'t have a {} field.'.format(self.type.title(),tag)
        self.description = ''
        
        self.form_class = SelectAuthority
        self.expected_params = ['authority']
        self.tag = tag

        self.field_names = ['Record Type', 'Record ID', 'Document Symbol']
    
    def execute(self,args):
        self.validate_args(args)
        
        body,session = _get_body_session(args['authority'])
        
        query = QueryDocument(
            Condition(self.symbol_field, ('b', body), ('c', session)),
            Condition('930', ('a', Regex('^' + self.type_code))),
            Condition(self.tag, modifier='not_exists'),
        )
        
        if self.type == 'bib' and self.tag == '992':
            query.add_condition(Condition(self.symbol_field, ('a', Regex(r'(SR\.|PV\.)'))))
        
        results = []
        
        for bib in BibSet.from_query(query, projection={self.symbol_field: 1, '930': 1}):            
            results.append([bib.get_value('930', 'a'), bib.id, bib.get_value(self.symbol_field, 'a')])
           
        return results
        
### Missing subfield type

class MissingSubfield(Report):
    def __init__(self,tag,code):
        self.name = '{}_missing_{}'.format(self.type,tag + code)
        self.title = 'Missing subfield - {}${}'.format(tag,code)
        #self.description = '{} records from the given body/session that don\'t have a {} field but no subfield ${}.'.format(self.type.title(),tag,code)
        self.description = ''

        self.tag = tag
        self.code = code
        self.form_class = SelectAuthority
        self.expected_params = ['authority']
        
        self.output_fields = [
            ('930','a'),
            ('001',None),
            (self.symbol_field,'a')
        ]
        self.field_names = ['Record Type','Record ID', 'Document Symbol', '991$a', '991$b', '991$c', '991$d', '991$e']
    
    def execute(self,args):
        self.validate_args(args)
        
        body,session = _get_body_session(args['authority'])
        
        query = QueryDocument(
            Condition(self.symbol_field, ('b', body), ('c', session)),
            Condition('930', ('a', Regex('^' + self.type_code))),
            Condition(self.tag, modifier='exists'),
            Condition(self.tag, (self.code, Regex('^.*')), modifier='not')
        )
        
        results = []
        
        for bib in BibSet.from_query(query, projection={self.symbol_field: 1, '930': 1}):
            results.append([bib.get_value('930', 'a'), str(bib.id), bib.get_value(self.symbol_field, 'a')] + [bib.get_value('991', x) for x in ('a', 'b', 'c', 'd', 'e')])
        
        return results

### Incorrect sesssion type

class IncorrectSession(Report):
    def __init__(self):
        self.name = '{}_incorrect_session_{}'.format(self.type, self.symbol_field)
        self.title = 'Incorrect session - {}'.format(self.symbol_field)
        self.description = ''
        
        self.form_class = SelectAuthority
        self.expected_params = ['authority']
        
        self.field_names = ['Record ID', *['{}${}'.format(self.symbol_field, x) for x in ('a', 'b', 'c', 'q', 'r')]]
        
    def execute(self, args):
        self.validate_args(args)
        body, session = _get_body_session(args['authority'])
        
        bibset = BibSet.from_query(
            QueryDocument(
                Condition(self.symbol_field, {'b': body, 'c': session}),
                Condition('930', {'a': Regex('^{}'.format(self.type_code))})
            ),
            projection={self.symbol_field: 1}
        )
        
        results = []
        
        for bib in bibset:
            for field in bib.get_fields(self.symbol_field):
                if _body_session_from_symbol(field.get_value('a')):
                    body, session = _body_session_from_symbol(field.get_value('a'))
                    
                    if field.get_value('r') != body + session:
                        row = [field.get_value(x) for x in ('a', 'b', 'c', 'q', 'r')]
                        row.insert(0, bib.id)
                        results.append(row)
                else:
                    warn('Body and session not detected in ' + field.get_value('a'))
                    
        return results

### 269 and 992 mismatch

class Mismatch269_992(Report):
    def __init__(self):
        self.name = '{}_field_mismatch_269_992'.format(self.type)
        self.title = "Field mismatch - 269 & 992"
        
        self.form_class = SelectAuthority
        self.expected_params = ['authority']
        
        self.field_names = ['Record ID', self.symbol_field + '$a', '269$a', '992$a']
        
    def execute(self, args):
        self.validate_args(args)
        body, session = _get_body_session(args['authority'])
        
        query = QueryDocument(
            Condition(self.symbol_field, {'b': body, 'c': session}),
            Condition('930', {'a': Regex('^{}'.format(self.type_code))})
        )
        
        results = []
        
        for bib in BibSet.from_query(query, projection={self.symbol_field: 1, '269': 1, '992': 1}):
            if bib.get_value('269', 'a') != bib.get_value('992', 'a'):
                results.append([bib.id, bib.get_value(self.symbol_field, 'a'), bib.get_value('269', 'a'), bib.get_value('992', 'a')])
                
        return results
        
### incorrect 991
  
class Incorrect991(Report):
    def __init__(self):
        self.name = '{}_incorrect_991'.format(self.type)
        self.title = "Incorrect field - 991"
        self.description = ""
        
        self.form_class = SelectAuthority
        self.expected_params = ['authority']
    
        self.field_names = ['Record ID', self.symbol_field + '$a', '991$a', '991$b', '991$c', '991$d', '991$e']
        
    def execute(self, args):
        self.validate_args(args)
        body, session = _get_body_session(args['authority'])
        
        query = QueryDocument(
            Condition(self.symbol_field, {'b': body, 'c': session}),
            Condition('930', {'a': Regex('^{}'.format(self.type_code))})
        )
        
        results = []
        
        for bib in BibSet.from_query(query, projection={self.symbol_field: 1, '991': 1}):
            for field in bib.get_fields('991'):
                aparts = field.get_value('a').split('/')
                syms = bib.get_values(self.symbol_field, 'a')
                found = 0
    
                for sym in syms:
                    try:
                        body, session = _body_session_from_symbol(sym)
                    except:
                        warn('Body and session not detected in ' + field.get_value('a'))
                
                    if aparts[0:2] == [body, session]:                
                        found += 1
        
                if found == 0: # and bib.get_value(self.symbol_field, 'a')[:4] != 'S/PV':
                    row = [field.get_value(x) for x in ('a', 'b', 'c', 'd', 'e')]
                    row.insert(0, '; '.join(syms))
                    row.insert(0, bib.id)
                    results.append(row)
                
        return results
        
### Incorrect 992

class Incorrect992(Report):
    def __init__(self):
        self.name = '{}_incorrect_field_992'.format(self.type)
        self.title = "Incorrect field - 992"
        self.description = ""
        
        self.form_class = SelectAuthority
        self.expected_params = ['authority']
        
        self.field_names = ['Symbol', '{} record ID'.format(self.type), '992$a', 'Bib record ID', '992$a']

    def execute(self, args):
        self.validate_args(args)
        body, session = _get_body_session(args['authority'])
        
        query = QueryDocument(
            Condition('791', {'b': body, 'c': session}),
            Condition('930', {'a': Regex('^{}'.format(self.type_code))})
        )
        
        results = []
        check = {}
        
        for bib in BibSet.from_query(query, projection={'791': 1, '992': 1}):
            sym = bib.get_value('791', 'a')
            date1 = bib.get_value('992', 'a')
            date2 = ''
            
            if sym in check:
                date2 = check[sym].get_value('992', 'a')
            else:
                to_check = Bib.find_one(QueryDocument(Condition('191', {'a': sym})).compile(), {'992': 1})
                
                if to_check:
                    date2 = to_check.get_value('992', 'a')
                    check[sym] = to_check
                else:
                    warn(sym + ' not found')
                    check[sym] = Bib()
                    
            if date1 != date2:
                results.append([bib.get_value('791', 'a'), bib.id, date1, check[sym].id, date2])
            
        return results
       
### Auth reports

class AgendaList(Report):
    def __init__(self):
        self.name = 'agenda_list'
        self.title = 'Agenda list'
        self.description = 'Agenda items from the given session'
        
        self.category = "OTHER"
        self.form_class = SelectAuthority
        self.expected_params = ['authority']
        
        self.output_fields = [('191', 'a'), ('991', 'b'), ('991', 'd')]
        self.field_names = ['Document Symbol', 'Auth#', 'Agenda Item No.', 'Agenda Subject']
        
    def execute(self,args):
        self.validate_args(args)
        body, session = _get_body_session(args['authority'])
        
        query = QueryDocument(
            Condition('191', {'a': Regex('^{}{}'.format(body, session))})
        )
        
        results = []
        
        for auth in AuthSet.from_query(query):
            results.append([auth.get_value('191', 'a'), auth.id, auth.get_value('191', 'b'), auth.get_value('191', 'd')])

        sorted_results = natsorted(results, key=lambda x: x[2])
        
        return sorted_results

### Bib reports
# These reports are on records that have field 191 and 930$a='UND'

class BibReport(Report):
    def __init__(self):
        self.type = 'bib'
        self.category = 'BIB'
        self.type_code = 'UND'
        self.symbol_field = '191'
     
class BibIncorrect793Comm(Report):
    def __init__(self):
        self.name = 'bib_incorrect_793_committees'
        self.title = 'Incorrect and/or missing field – 793 (Committees)'
        self.description = 'Bib records where 191 starts with "A/C.<committee number>" and 793$a does not equal the committe number'

        self.form_class = SelectAuthority
        self.expected_params = ['authority']
        
        self.field_names = ['Document Symbol']
        
        BibReport.__init__(self)
        
    def execute(self,args):
        self.validate_args(args)
     
        body,session = _get_body_session(args['authority'])
        
        query = QueryDocument(
            Condition('191', ('b',body), ('c',session)),
            Condition('930', ('a', Regex('^UND'))),
        )
        
        results = []
        
        for bib in BibSet.from_query(query, projection={'191': 1, '793': 1}):
            m = re.match(r'^A/C\.(\d)/', bib.get_value('191', 'a'))
            
            if m and bib.get_value('793','a') != '0' + m.group(1):
                results.append([bib.get_value('191', 'a')]) 

        return results
       
class BibIncorrect793Plen(BibReport):
    def __init__(self):
        self.name = 'bib_incorrect_793_plenary'
        self.title = 'Incorrect and/or missing field – 793 (Plenary)'
        self.description = 'Bib records where 191 starts with "A/RES" or "A/<session>/L." and 793$a does not equal with "PL"'
        
        self.form_class = SelectAuthority
        self.expected_params = ['authority']
        
        self.field_names = ['Document Symbol']
        
        BibReport.__init__(self)
        
    def execute(self,args):
        self.validate_args(args)
     
        body,session = _get_body_session(args['authority'])
        
        query = QueryDocument(
            Condition('191', ('b',body), ('c',session)),
            Condition('930', ('a', Regex('^UND'))),
        )
        
        results = []
        
        for bib in BibSet.from_query(query, projection={'191': 1,'793': 1}):
            if re.match(r'^A/RES/', bib.symbol()) or re.match(r'^A/' + session + r'/L\.', bib.symbol()):
                if bib.get_value('793', 'a') != 'PL':
                    results.append([bib.get_value('191', 'a')]) 

        return results

class BibMissingField(BibReport, MissingField):
    def __init__(self, tag):
        BibReport.__init__(self)
        MissingField.__init__(self, tag)
        
class BibMissingSubfield(BibReport, MissingSubfield):
    def __init__(self, tag, code):
        BibReport.__init__(self)
        MissingSubfield.__init__(self, tag, code)

class BibIncorrect991(BibReport, Incorrect991):
    def __init__(self):
        BibReport.__init__(self)
        Incorrect991.__init__(self)
        
class BibIncorrectSession191(BibReport, IncorrectSession):
    def __init__(self):    
        BibReport.__init__(self)
        IncorrectSession.__init__(self)
        
class BibIncorrectSubfield191_9(BibReport):
    def __init__(self):    
        self.name = 'bib_incorrect_subfield_191_9'
        self.title = 'Incorrect subfield - 191$9'
        self.description = ''
        
        self.form_class = SelectAuthority
        self.expected_params = ['authority']
       
        self.field_names = ['Record ID', '191$a', '191$9']
        
        BibReport.__init__(self)
        
    def execute(self, args):
        self.validate_args(args)
        body, session = _get_body_session(args['authority'])
        
        bibset = BibSet.from_query(
            QueryDocument(
                Condition('191', {'b': body, 'c': session}),
                Condition('930', {'a': Regex('^UND')})
            ),
            projection={'191': 1}
        )
        
        results = []

        for bib in bibset:
            for field in bib.get_fields('191'):
                sym = field.get_value('a')
                flag = False
                
                if sym[:1] == 'A':
                    if field.get_value('9')[:1] != 'G':
                        flag = True
                elif sym[:1] == 'E':
                    if field.get_value('9')[:1] != 'C':
                        flag = True
                elif sym[:1] == 'S':
                    if field.get_value('9')[:1] != 'X':
                        flag = True
                
                if flag is True:
                    results.append([bib.id, field.get_value('a'), field.get_value('9')])
                    
        return results
        
class BibMissingSubfieldValue(BibReport):
    def __init__(self, tag, code, value):
        BibReport.__init__(self)
        
        self.name = 'bib_missing_subfield_value_{}_{}_{}'.format(tag, code, value)
        self.title = 'Missing subfield value - {}${} {}'.format(tag, code, value)
        self.description = ''
        
        self.tag = tag
        self.code = code
        self.value = value
        
        self.form_class = SelectAuthority
        self.expected_params = ['authority']
        
        self.name = 'bib_missing_subfield_value_{}_{}_{}'.format(tag, code, value)
        self.title = 'Missing subfield value - {}${} {}'.format(tag, code, value)
        
        self.field_names = ['Record ID', '191$a', '{}${}'.format(tag, code)]
        
        if self.tag == '991':
            for x in ('a', 'b', 'c', 'd', 'e'):
                self.field_names.append('991${}'.format(x))
                                                        
    def execute(self, args):
        self.validate_args(args)
        body, session = _get_body_session(args['authority'])
        
        query = QueryDocument(
            Condition('191', {'b': body, 'c': session}),
            Condition('930', {'a': Regex('^UND')}),
            Condition(self.tag, {self.code: Regex('^.*')}),
            Condition(self.tag, {self.code: self.value}, modifier='not')
        )
        
        if self.tag == '991' and self.code == 'f' and self.value == 'X27':
            query.add_condition(
                Condition('991', {'e': Regex('Participation')})
            )
        
        results = []
        
        for bib in BibSet.from_query(query, projection={'191': 1, self.tag: 1}):
            row = [bib.id, '; '.join(bib.get_values('191', 'a'))]
            row.append(bib.get_value(self.tag, self.code))
            
            if self.tag == '991':
                for x in ('a', 'b', 'c', 'd', 'e'):
                    row.append(bib.get_value(x))
            
            results.append(row)

        return results

### Speech reports
# These reports are on records that have 791 and 930="ITS"
class SpeechReport(Report):
    def __init__(self):
        self.category = "SPEECH"
        self.type = 'speech'
        self.type_code = 'ITS'
        self.symbol_field = '791'
    
class SpeechMissingField(SpeechReport, MissingField):
    def __init__(self, tag):
        SpeechReport.__init__(self)
        MissingField.__init__(self, tag)
        
class SpeechMissingSubfield(SpeechReport, MissingSubfield):
    def __init__(self,tag,code):
        SpeechReport.__init__(self)
        MissingSubfield.__init__(self, tag, code)

class SpeechDuplicateRecord(SpeechReport):
    def __init__(self):
        SpeechReport.__init__(self)
        
        self.name = 'speech_duplicate_record'
        self.title = "Duplicate speech records"
        
        self.form_class = SelectAuthority
        self.expected_params = ['authority']

        self.field_names = ['791$a', '700$a', '710$a', '711$a', '991$d', 'Record IDs'] 
        
    def execute(self, args):
        self.validate_args(args)
        body, session = _get_body_session(args['authority'])
        
        query = QueryDocument(
            Condition('791', {'b': body, 'c': session}),
            Condition('930', {'a': Regex('^ITS')})
        )
        
        seen = {}
        results = []
        count = Counter()
        
        for bib in BibSet.from_query(query, projection={'269': 1, '700': 1, '710': 1, '711': 1, '791': 1, '991': 1}):
            vals = [
                bib.get_value('269', 'a'),
                *[str(x) for x in bib.get_xrefs('700')],
                *[str(x) for x in bib.get_xrefs('710')],
                *[str(x) for x in bib.get_xrefs('711')],
                bib.get_value('791', 'a'),
                *[str(x) for x in bib.get_xrefs('991')]
            ]
            
            key = ';'.join(vals)
            
            if key in seen: 
                seen[key].append(bib.id)
            else:
                seen[key] = [bib.id]
            
        for key in seen.keys():
            if len(seen[key]) > 1:
                ids = seen[key]
                bib = Bib.match_id(ids[0])
                results.append([bib.get_value('791', 'a'), bib.get_value('700', 'a'), bib.get_value('710', 'a'), bib.get_value('711', 'a'), bib.get_value('991', 'd'), ids])
        
        return results

class SpeechIncompleteAuthority(SpeechReport):
    def __init__(self):
        SpeechReport.__init__(self)
        self.name = 'speech_incomplete_authority'
        self.title = "Incomplete authorities"
        self.description = "Authority records referenced from speech record fields 700, 710, or 711 that are missing 905 or 915"
        
        self.form_class = SelectAuthority
        self.expected_params = ['authority']

        self.field_names = ['Authory ID', 'Heading', '905$a', '915$a']
    
    def execute(self, args):
        self.validate_args(args)
        body, session = _get_body_session(args['authority'])
        
        query = QueryDocument(
            Condition('791', {'b': body, 'c': session}),
            Condition('930', {'a': Regex('^ITS')})
        )
        
        auth_ids = []
        
        for bib in BibSet.from_query(query, projection={'700': 1, '710': 1, '711': 1}):
            auth_ids += bib.get_xrefs('700') + bib.get_xrefs('710') + bib.get_xrefs('711')
            
        query = {'_id': {'$in': auth_ids}}
        results = []
        
        for auth in AuthSet.from_query(query, projection={'100': 1, '110': 1, '111': 1, '905': 1, '915': 1}):
            if not auth.get_field('905') or not auth.get_field('915'):
                results.append([auth.id, auth.heading_value('a'), auth.get_value('905', 'a'), auth.get_value('915', 'a')])
            
        return results

class SpeechMismatch(SpeechReport, Mismatch269_992):
    def __init__(self):
        SpeechReport.__init__(self)
        Mismatch269_992.__init__(self)
        
class SpeechIncorrect991(SpeechReport, Incorrect991):
    def __init__(self):
        SpeechReport.__init__(self)
        Incorrect991.__init__(self)

class SpeechIncorrect992(SpeechReport, Incorrect992):
    def __init__(self):
        SpeechReport.__init__(self)
        Incorrect992.__init__(self)
        
class SpeechIncorrectSession791(SpeechReport, IncorrectSession):
    def __init__(self):    
        SpeechReport.__init__(self)
        IncorrectSession.__init__(self)
            
### Vote reports
# These reports are on records that have 791 and 930="VOT"

class VoteReport(Report):
    def __init__(self):
        self.type = 'vote'
        self.category = 'VOTING'
        self.type_code = 'VOT'
        self.symbol_field = '791'

class VoteIncorrectSession(VoteReport, IncorrectSession):
    def __init__(self):
        VoteReport.__init__(self)
        IncorrectSession.__init__(self)

class VoteMissingField(VoteReport, MissingField):
    def __init__(self, tag):
        VoteReport.__init__(self)
        MissingField.__init__(self, tag)

class VoteMissingSubfield(VoteReport, MissingSubfield):
    def __init__(self,tag,code):
        VoteReport.__init__(self)
        MissingSubfield.__init__(self, tag, code)
        
class VoteMismatch(VoteReport, Mismatch269_992):
    def __init__(self):
        VoteReport.__init__(self)
        Mismatch269_992.__init__(self)
  
class VoteIncorrect991(VoteReport, Incorrect991):
    def __init__(self):
        VoteReport.__init__(self)
        Incorrect991.__init__(self)
        
class VoteIncorrect992(VoteReport, Incorrect992):
    def __init__(self):
        VoteReport.__init__(self)
        Incorrect992.__init__(self)

### "Other" reports

class AnyMissingField(Report):
    def __init__(self,tag):
        #super().__init__(self)
        
        self.name = 'any_missing_field'
        self.tag = tag
        self.title = 'Missing Field - ' + tag
        self.description = 'Any records from the given body/session that don\'t have a {} field.'.format(tag)
        self.category = "OTHER"
        self.form_class = SelectAuthority
        
        self.expected_params = ['authority']
       
        self.output_fields = [
            ('930','a'),
            ('001',None),
            ('191','a'),
            ('791','a')
        ]
        
        self.field_names = ['Record Type','Record ID', 'Document Symbol']
        
    def execute(self,args):
        self.validate_args(args)
    
        body,session = _get_body_session(args['authority'])
    
        bibs = Bib.match(
            OrMatch(
                Condition('191',('b',body),('c',session)),
                Condition('791',('b',body),('c',session))
            ),
            Condition(self.tag,modifier='not_exists'),
            project=[f[0] for f in self.output_fields]
        )
    
        # list of lists
        results = _process_results(bibs,self.output_fields)
        
        # combine 191 and 791
        
        return list(map(lambda row: [row[0], row[1], row[2] + row[3]], results))
        
### For use by the main app to access the reports
     
class ReportList(object):
    reports = [
        
        # predefined reports
        
        # bib category 
        
        # Agenda List
        AgendaList(), #WIP
        # Incorrect field - 793 (Committees)
        BibIncorrect793Comm(),
        # Incorrect field - 793 (Plenary)
        BibIncorrect793Plen(),
        # Incorrect field - 991
        BibIncorrect991(),
        # Incorrect session - 191
        BibIncorrectSession191(),
        # Incorrect subfield - 191$9
        BibIncorrectSubfield191_9(),
        # Missing field - 793
        #BibMissingField('793'), # *** disable as per VW - same as "Incorrect"
        # Missing field - 991
        BibMissingField('991'),
        # Missing field - 992
        BibMissingField('992'),
        # Missing subfield - 191$9
        #BibMissingSubfield('191','9'), # *** disable as per VW - same as "Incorrect"
        # Missing subfield - 991$d
        BibMissingSubfield('991','d'),
        
        # Missing subfield value - 991$f X27
        BibMissingSubfieldValue('991', 'f', 'X27'),
        # Missing subfield value - 991$z I
        BibMissingSubfieldValue('991', 'z', 'I'),
        # Missing subfield value - 999$c t
        BibMissingSubfieldValue('999', 'c', 't'),
        
        BibMissingField('930'),
        
        # speech category 
        
        # Duplicate speech records
        SpeechDuplicateRecord(),
        # Field mismatch - 269 & 992
        SpeechMismatch(),
        
        # Incomplete authorities
        SpeechIncompleteAuthority(),
        # Incorrect field - 991
        SpeechIncorrect991(),
        # Incorrect field - 992
        SpeechIncorrect992(),
        # Incorrect session - 791
        SpeechIncorrectSession791(),
        # Missing field - 039
        SpeechMissingField('039'),
        # Missing field - 856
        SpeechMissingField('856'),
        # Missing field - 991
        SpeechMissingField('991'),
        # Missing field - 992
        SpeechMissingField('992'),
        # Missing subfield - 991$d
        SpeechMissingSubfield('991','d'),
        
        SpeechMissingField('930'),
    
        # voting category

        # Field mismatch - 269 & 992
        VoteMismatch(),
        # Incorrect field - 991
        VoteIncorrect991(),
        # Incorrect field - 992
        VoteIncorrect992(),
        # Incorrect session - 791
        VoteIncorrectSession(),
        # Missing field - 039
        VoteMissingField('039'),
        # Missing field - 856
        VoteMissingField('856'),
        # Missing field - 991
        VoteMissingField('991'),
        # Missing field - 992
        VoteMissingField('992'),
        # Missing subfield - 991$d
        VoteMissingSubfield('991','d'),
        
        VoteMissingField('930')
        
        # other
        
        # Missing field - 930
        # AnyMissingField('930'),
        # Missing field - any
        # Missing subfield - any
    ]
    
    def get_by_name(name):
        return next(filter(lambda r: name == r.name, ReportList.reports), None)
        
### exceptions

class AuthNotFound(Exception):
    def __init__(self, msg):
        super().__init__(msg)

class InvalidInput(Exception):
    def __init__(self, msg):
        super().__init__(msg)

### utility functions

def _get_body_session(string):
    try:
        auth_id = int(string)
        auth = Auth.match_id(auth_id)
    except ValueError:
        try:
            body,session = string.split('/')
            auth = next(Auth.match(Condition('190',('b',body+'/'),('c',session))),None)
        except ValueError:
            raise InvalidInput('Invalid session')
        
    if auth is None:
        raise AuthNotFound('Session authority not found')
    else:
        body = auth.get_value('190','b')
        session = auth.get_value('190','c')
        
    return (body,session)
    
def _process_results(generator, output_fields):
    results = []
    
    for bib in generator:
        results.append(['; '.join(bib.get_values(*out)) for out in output_fields])
    
    # list of lists
    return results
    
def _body_session_from_symbol(symbol):
    sparts = symbol.split('/')
    body = sparts[0]
    session = None
                    
    if body == 'A':
        if sparts[1][0:1] == 'C' or sparts[1] in ['RES', 'INF', 'BUR', 'DEC']: 
            session = sparts[2]
        else:
            session = sparts[1]
    elif body == 'S':
        if sparts[1] in ['PRST']:
            year = sparts[2][:4]
        elif sparts[1]== 'RES':
            match = re.search(r'\((.+)\)', symbol)
            year = match.group(1)
        elif re.match(r'\d\d\d\d$', sparts[1]):
            year = sparts[1]
        else:
            return
        
        try:
            session = _sc_convert(year)
        except(ValueError):
            warn('could not read ' + symbol)
            return       
    elif body == 'E':
        if sparts[1]== 'RES': 
            session = sparts[2]
        else:
            session = sparts[1]
    else:
        return
        
    return [body, session]


def _sc_convert(year):
    return str(int(year) - 1945)
