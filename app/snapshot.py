import re
import json
from app.config import Config
from app.forms import MissingFieldReportForm, MissingSubfieldReportForm, SelectAuthority
#import dlx
from dlx import DB
from dlx.marc import Bib, Auth, BibSet, QueryDocument,Condition,Or
import pymongo
from pymongo import MongoClient, ReplaceOne
import time


DB.connect(Config.connect_string)
db_client=MongoClient(Config.connect_string)
db=db_client['undlFiles']
rules_coll = db['dev_Itpp_document']
snapshot_coll=db['itpp_snapshot_test3']
itp_bib_fields=[]


class Snapshot(object):
    def __init__(self,body, session):
        self.name = None
        self.title = None
        self.description = "New Snapshot"
        #self.form_class = SelectAuthority
        self.expected_params = "authority"
        self.body=body
        self.session=session
        #self.snapshot_len = None
        self.replace_list_recs=[]
        self.snapshots_list=[]


    def fields_to_extract(self):
        itp_bib_fields=[]
        i=0
        #1. get the list of fields for the ITP
        itp_bib_fields=[]
        itp_docs_count=rules_coll.count_documents({"$and":[{"body":self.body},{"itp_session":self.session},{"name":self.body+self.session}]})
        if itp_docs_count==1:
            for itp_doc in rules_coll.find({"$and":[{"body":self.body},{"itp_session":self.session},{"name":self.body+self.session}]}):
                for section in itp_doc["sections"]:
                    #print(f"section name is:{section['name']}")
                    for rule in section["rules"]:
                        if rule["name"]=="fields_needed":
                            for field_list in rule["parameters"]:
                                #print(f"    field_list is:{field_list}\n")
                                for fld in field_list.split(";"):
                                    itp_bib_fields.append(fld.strip()) 
                #print(f"from MDB: ->{sorted(set(itp_bib_fields))}")
                #print(f"Number of fields is {len(set(itp_bib_fields))}")
        else:
            itp_bib_fields=['001', '035$a', '591$a','700$a', '700$g', '710$a', '711$a', '791$a','791$b','791$c','793$a' '930$a', '949$a','991$b', '991$d','001', '035$a', '089$b', '191$9', '191$a', '191$b', '191$c','191$d', '191$z', '239$a', '245$a', '245$b', '245$c', '248$a', '249$a', '269$a', '495$a', '515$a', '520$a', '580$a', '591$a', '592$a', '598$a', '599$a', '791$b', '791$c', '930$a', '949$a', '991$a', '991$b', '991$c', '991$d', '991$e', '991$m', '991$s', '991$z', '992$a', '995$a', '996$a']
        set_itp_bib_fields=sorted(set(itp_bib_fields))
        print(f"fields are: {set_itp_bib_fields}")   
            
        # 2. prepare a proper structure of tuples for easier processing e.g.
        # [[(035,a)], [(089,a)], [(191,9), (191,a),  (191,b),  (191,c)]]
       
        set_itp_flds=set(map(lambda x:x.split("$")[0], set_itp_bib_fields))
        proj_dict={}
        for k in set_itp_flds:
            proj_dict[k]=True

        
        sbflds=[]
        itpp_bib_fields=[]

        f=''
        for itp_field in set_itp_bib_fields:
            if itp_field !="001":
                if itp_field.split("$")[0] !=f:
                    temp_f=[]
                    temp_f.append((itp_field.split("$")[0],itp_field.split("$")[1]))
                    itpp_bib_fields.append(temp_f)
                else:
                    temp_f.append((itp_field.split("$")[0],itp_field.split("$")[1]))
                f=itp_field.split("$")[0]
                s_f=itp_field.split("$")[1]
                #print(f"proj_dict: {proj_dict}") 
                #print(f"itpp_bib_fields: {itpp_bib_fields}")    
        return proj_dict,itpp_bib_fields


    def fetch_data(self,proj_dict):
        query = QueryDocument(
            Or(
                Condition(
                tag='191',
                subfields={'b': self.body+'/','c':self.session}
                    ),
                Condition(
                tag='791',
                subfields={'b': self.body+'/','c':self.session}
                    ),
                Condition(
                tag='930',
                subfields={'a': 'ITP'+self.body+self.session}
                    )
                )
            )
        #print(query.to_json())
        bibset=BibSet.from_query(query, projection=proj_dict, skip=0, limit=0)
        
        #l_temp=bibset.count
        #self.snapshot_len=l_temp 
        lbibs=list(bibset.records)
        print(f"the bibset length is : {len(lbibs)}")
        return lbibs#, l_temp
   
    ''' get the subfield values'''
    def list_of_subfields(self,bib,field,sbflds):
        temp_lst=[]
        
        for field in bib.get_fields(field):
            temp_dict={}
            for sub in field.subfields:
                if sub.code in sbflds:
                    temp_dict[sub.code]= sub.value
            temp_lst.append(temp_dict)
        return temp_lst

      
    ''' not used any more'''           
    def execute_bibs(self):
        proj_dict,itpp_bib_fields=self.fields_to_extract()
        lbibs,l_temp=self.fetch_data(proj_dict)
        return lbibs,itpp_bib_fields
    '''transforming data to snapshot schema'''
    def process_bib_records(self,chunk_no,no_of_chunks,lbibs,itpp_bib_fields):
        chunk_size=len(lbibs)//(no_of_chunks-1)
        if chunk_no==(no_of_chunks-1):
            end_rec=len(lbibs)
        else:
            end_rec=(chunk_no)*chunk_size

        for bib in lbibs[(chunk_no-1)*chunk_size:end_rec]:
            bib_dict={}
            if "ITS" in bib.get_values('930','a'):
                bib_dict["record_type"]="ITS"
            elif "VOT" in bib.get_values('930','a'):
                bib_dict["record_type"]="VOT"
            else:
                bib_dict["record_type"]="BIB"

            bib_dict["record_id"]=bib.id
            bib_dict["bodysession"]=self.body+'/'+self.session

            for itpp_field_subfields in itpp_bib_fields:
                sbflds=[]
                for elem in itpp_field_subfields:
                    field=elem[0]
                    sbflds.extend(elem[1])
                temp_dict={}
                temp_dict[field]=self.list_of_subfields(bib,field,sbflds)
                if len(temp_dict[field])>1:
                    bib_dict[field]=temp_dict[field]
                elif len(temp_dict[field])==1:
                    bib_dict[field]=temp_dict[field][0]
                else:
                    bib_dict[field]=""
            #snapshot_list_bibs.append(bib_dict)
            query={"record_id":bib_dict["record_id"]}
            self.replace_list_recs.append(ReplaceOne(query, bib_dict, upsert=True))
        return len(self.replace_list_recs)
    ''' writing data into snapshot collection'''    
    def bulk_write_bibs(self):
        try:
            print("No. of replace_list_bibs records is: {}".format(len(self.replace_list_recs)))
            snapshot_coll.bulk_write(self.replace_list_recs)
        except:
                warning="something went wrong with insert into MDb"    
    '''asycnh fuinction'''
    def transform_write(self):
        proj_dict,itpp_bib_fields=self.fields_to_extract()
        #lbibs,l_temp=self.fetch_data(proj_dict)
        lbibs=self.fetch_data(proj_dict)
        no_of_chunks=10
        for i in range(1,no_of_chunks+1):
            start_time_chunk=time.time()
            len1=self.process_bib_records(i,no_of_chunks+1,lbibs,itpp_bib_fields)
            print(f"--- {time.time() - start_time_chunk} seconds for chunk {i} run ---")
            print(f"chunk No is: {i}; records processed are {len1}")
            #print("No. of ITP its records is: {}".format(number_itss))
        print(f"No. of ITP records is: {len(self.replace_list_recs)}")
        start_time_bulk_write=time.time()
        self.bulk_write_bibs()
        print(f"--- {time.time() - start_time_bulk_write} seconds for bulk write for {self.body}/{self.session} ---")

    ''' listing snapshots in display snapshot'''
    def list(self):
        snapshots=snapshot_coll.distinct("bodysession")
        for sh in snapshots:
            self.snapshots_list.append((sh,snapshot_coll.find_one({'bodysession':sh},sort=[( '_id', pymongo.DESCENDING )])['_id'].generation_time.strftime("%Y-%m-%d %H:%M:%S")))
        return sorted(self.snapshots_list,key=lambda x: x[1], reverse = True)






