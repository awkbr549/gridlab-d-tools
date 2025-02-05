'''
Created on Nov 19, 2013

@author: fish334

PREREQS!!!!!! sudo apt-get install mdbtools libmdbodbc1

This script converts a CYME feeder model database to an OMF feeder tree dictionary object. The out put is similar to that produced by milToGridlab.py

An example of how to call the script is shown below:
    import convert_cyme_model
    feederTree = convert_cyme_model.convertCymeModel(db_network, db_equipment, id_feeder, conductors)

where:

db_network is the full path to the CYME network .mdb database file.
db_equipment is the full path to the CYME equipment .mdb database file.
id_feeder is a string of the NetworkId associated with the particular feeder.
conductors is the full path to a .csv file containing conductor information for underground concentric neutral and tape shield cables used in the CYME model.

Note that db_network and db_equipment can be the same file is both network and equipment databases were exported to one .mdb file from CYME.
'''
import pyodbc
import csv, random, math, copy
import warnings
from io import StringIO
import sys, os, json, traceback, shutil
from pathlib import Path
import re

m2ft = 1.0/0.3048             # Conversion factor for meters to feet

def _openDatabase(database_file):
    '''Function that creates a connection to a .mdb database file'''
    if sys.platform == 'win32' or sys.platform == 'cygwin':
        #Windows Driver: {Microsoft Access Driver (*.mdb)}        
        connect_string = 'DRIVER={Microsoft Access Driver (*.mdb, *.accdb)};DBQ=' + str(database_file) + ';'
    elif sys.platform == 'darwin':
        # Mac
        connect_string = 'DRIVER={MDBTools};DBQ=' + str(database_file) + ';'    
    elif sys.platform == 'linux2':
        #Linux Driver: {MDBTools}           
        connect_string = 'DRIVER={MDBTools};DBQ=' + str(database_file) + ';'    
    print("connect string =",(connect_string))
    database_connection = pyodbc.connect(connect_string)
    database = database_connection.cursor()
    return database

def _fixName(name):
    '''Function that replaces characters not allowed in name with _'''
    badChar = [' ', '-', '\\', '//', '/', ':', '.', "'\'", '&']
    for char in badChar:
        name = name.replace(char, '_')
    return name

def _convertPhase(int_phase):
    '''Function that converts a number to a phase'''
    if int_phase == 1:
        phase = 'AN'
    elif int_phase == 2:
        phase = 'BN'
    elif int_phase == 3:
        phase = 'CN'
    elif int_phase == 4:
        phase = 'ABN'
    elif int_phase == 5:
        phase = 'ACN'
    elif int_phase == 6:
        phase = 'BCN'
    elif int_phase == 7:
        phase = 'ABCN'
    else:
        phase = None
    return phase

def _convertRegulatorPhase(int_phase):
    '''Function that converts a number to a phase'''
    if int_phase == 1:
        phase = 'A'
    elif int_phase == 2:
        phase = 'B'
    elif int_phase == 3:
        phase = 'C'
    elif int_phase == 4:
        phase = 'AB'
    elif int_phase == 5:
        phase = 'AC'
    elif int_phase == 6:
        phase = 'BC'
    elif int_phase == 7:
        phase = 'ABC'
    else:
        phase = None           
    return phase

def _convertLoadClass(class_from_db):
    '''# Function the converts a load classification string to a number'''
    classes = {}
    classes['Residential1'] = 0
    classes['Residential2'] = 1
    classes['Residential3'] = 2
    classes['Residential4'] = 3
    classes['Residential5'] = 4
    classes['Residential6'] = 5
    classes['Commercial1'] = 6
    classes['Commercial2'] = 7
    classes['Commercial3'] = 8
    if class_from_db in list(classes.keys()):
        return classes[class_from_db]
    else:
        return None

def _csvToArray(csvFileName):
    ''' Simple .csv data ingester. '''
    with open(csvFileName,'r') as csvFile:
        csvReader = csv.reader(csvFile)
        outArray = []
        for row in csvReader:
            outArray += [row]
        return outArray

def _readCymeSource(networkDatabase,feederId, type):
    '''store information for the swing bus. The swing bus is assumed to be 
    located in the CYMSOURCE table of the database. These are the expected 
    columns that are read. NodeId, NetworkId, EquipmentId, DesiredVoltage. The 
    only row that is stored is the row who's NetworkId == feederId. 
    DesiredVoltage is assumed to the line to line voltage in kV.'''
    cymsource = {}                          # Stores information found in CYMSOURCE or CYMEQUIVALENTSOURCE in the network database    
    if (type==1):
        CYMSOURCE = { 'name' : None,            # information structure for each object found in CYMSOURCE
                                'bustype' : 'SWING',
                                'nominal_voltage' : None,
                                'phases' : None}
        # Check to see if the network database contains models for more than one database and if we chose a valid feeder_id to convert
        try:
            feeder_db = networkDatabase.execute("SELECT NodeId, NetworkId, EquipmentId, DesiredVoltage FROM CYMSOURCE").fetchall()
        except:
            feeder_db = networkDatabase.execute("SELECT NodeId, NetworkId, EquipmentId, OperatingVoltageA FROM CYMSOURCE").fetchall()

    elif (type==2):
        CYMEQUIVALENTSOURCE = { 'name' : None,            # information structure for each object found in CYMEQUIVALENTSOURCE
                                'bustype' : 'SWING',
                                'nominal_voltage' : None,
                                'phases' : None}
        # Check to see if the network database contains models for more than one database and if we chose a valid feeder_id to convert
        feeder_db = networkDatabase.execute("SELECT NodeId, OperatingVoltage1 FROM CYMEQUIVALENTSOURCE").fetchall()
        feeder_db_net =  networkDatabase.execute("SELECT NetworkId FROM CYMNETWORK").fetchall()   
        if (feeder_db_net == None): 
            raise RuntimeError("No source node was found in CYMSOURCE: {:s}.\n".format(feederId))            
    if feeder_db == None:
        raise RuntimeError("No source node was found in CYMSOURCE: {:s}.\n".format(feederId))
    else:            
        try: 
            print("NetworkId", feeder_db_net) 
        except:
            pass                 
    '''mj debug'''
    if feederId == None:
        '''mj debug'''
        print("NO FEEDER ID\n") 
        if len(feeder_db) >= 1:
            if len(feeder_db) == 1:
                try:
                    for row in feeder_db:
                        feeder_id = row.NetworkId
                        cymsource[_fixName(row.NodeId)] = copy.deepcopy(CYMSOURCE)
                        cymsource[_fixName(row.NodeId)]['name'] = _fixName(row.NodeId)
                        try:
                            cymsource[_fixName(row.NodeId)]['nominal_voltage'] = str(float(row.DesiredVoltage)*1000.0/math.sqrt(3))
                        except:
                            cymsource[_fixName(row.NodeId)]['nominal_voltage'] = str(float(row.OperatingVoltageA)*1000.0)
                except:
                    for row in feeder_db_net:
                        feeder_id = row.NetworkId
                        cymsource[_fixName(row.NodeId)] = copy.deepcopy(CYMEQUIVALENTOURCE)
                        cymsource[_fixName(row.NodeId)]['name'] = _fixName(row.NodeId)
                        cymsource[_fixName(row.NodeId)]['nominal_voltage'] = str(float(row.OperatingVoltage1)*1000.0/math.sqrt(3))                        
            else:
                raise RuntimeError("The was no feeder id given and the network database contians more than one feeder. Please specify a feeder id to extract.")
    else:
        '''mj debug'''
        print("FEEDER ID", feederId) 
        feederIds = []
        if (type==1):
            for row in feeder_db:
                feederIds.append(row.NetworkId) 
            if feederId not in feederIds:
                raise RuntimeError("The feeder id provided is not in the network database. Please specify a valid feeder id to extract.")
            for row in feeder_db:
                if row.NetworkId == feederId:
                    feeder_id = feederId
                    cymsource[_fixName(row.NodeId)] = copy.deepcopy(CYMSOURCE)
                    cymsource[_fixName(row.NodeId)]['name'] = _fixName(row.NodeId)
                    try:
                        cymsource[_fixName(row.NodeId)]['nominal_voltage'] = str(float(row.DesiredVoltage)*1000.0/math.sqrt(3))
                    except:
                        cymsource[_fixName(row.NodeId)]['nominal_voltage'] = str(float(row.OperatingVoltageA)*1000.0)
                    swingBus = _fixName(row.NodeId)     
        elif (type==2):
            for row in feeder_db_net:
                feederIds.append(row.NetworkId)  
            if feederId not in feederIds:
                raise RuntimeError("The feeder id provided is not in the network database. Please specify a valid feeder id to extract.")
            for row in feeder_db_net:             
                if row.NetworkId == feederId:
                    feeder_id = feederId
            feederId_equivalent = "SOURCE_" + feeder_id          
            for row in feeder_db:         
                if feederId_equivalent in row.NodeId:
                    feeder_id = feederId
                    cymsource[_fixName(row.NodeId)] = copy.deepcopy(CYMEQUIVALENTSOURCE)
                    cymsource[_fixName(row.NodeId)]['name'] = _fixName(row.NodeId)
                    cymsource[_fixName(row.NodeId)]['nominal_voltage'] = str(float(row.OperatingVoltage1)*1000.0/math.sqrt(3))
                    swingBus = _fixName(row.NodeId)    

    return cymsource, feeder_id, swingBus

def _readCymeNode(networkDatabase, feederId):
    '''store lat/lon information on nodes. The x and y coordinates for all nodes
    on the feeder are expected to be located in CYMNODE. The expected columns 
    that are read are NodeId, X, and Y.'''
    # Helper for lat/lon conversion.
    x_list = []
    y_list = []
    x_pixel_range = 1200
    y_pixel_range = 800
    cymnode = {}
    CYMNODE = {'name' : None,
                            'latitude' : None,
                            'longitude' : None}
    node_db = networkDatabase.execute("SELECT NodeId, X, Y FROM CYMNODE WHERE NetworkId = '{:s}'".format(feederId)).fetchall()
    if len(node_db) == 0:
        warnings.warn("No information node locations were found in CYMNODE for feeder id: {:s}.".format(feederId), RuntimeWarning)
    else:
        for row in node_db:
            x_list.append(row.X)
            y_list.append(row.Y)
        try:
            x_scale = x_pixel_range / (max(x_list) - min(x_list))
            x_b = -x_scale * min(x_list)
            y_scale = y_pixel_range / (max(y_list) - min(y_list))
            y_b = -y_scale * min(y_list)
        except:
            x_scale,x_b,y_scale,y_b = (0,0,0,0)
        for row in node_db:
            row.NodeId = _fixName(row.NodeId)
            if row.NodeId not in list(cymnode.keys()):
                cymnode[row.NodeId] = copy.deepcopy(CYMNODE)
                cymnode[row.NodeId]['name'] = row.NodeId
                cymnode[row.NodeId]['latitude'] = str(x_scale * float(row.X) + x_b)
                cymnode[row.NodeId]['longitude'] = str(800 -(y_scale * float(row.Y) + y_b))      
    return cymnode, x_scale, y_scale
    
def _readCymeOverheadByPhase(networkDatabase, feederId, olc=None):
    '''store information from CYMOVERHEADBYPHASE. Unique overhead line 
    configuration equipment is expected to be located in CYMOVERHEADBYPHASE. The
    expected columns that are read are DeviceNumber, PhaseConductorIdA, 
    PhaseConductorIdB, PhaseConductorIdC, NeutralConductorId, 
    ConductorSpacingId, and Length. Length is assumed to be in meters.'''
    cymoverheadbyphase = {}     # Stores information found in CYMOVERHEADBYPHASE in the network database
    if olc is None:
        olc = {}
    thisolc = {}
    olcDevice = {}              # store device information for each configuration
    uniqueSpacing = []
    overheadConductors = []     # Stores the unique conductor equipment Ids
    CYMOVERHEADBYPHASE = { 'name' : None,       # Information structure for each object found in CYMOVERHEADBYPHASE
                          'length' : None,
                          'configuration' : None}
    overheadbyphase_db = networkDatabase.execute("SELECT DeviceNumber, PhaseConductorIdA, PhaseConductorIdB, PhaseConductorIdC, NeutralConductorId, ConductorSpacingId, Length FROM CYMOVERHEADBYPHASE WHERE NetworkId = '{:s}'".format(feederId)).fetchall()
    if len(overheadbyphase_db) == 0:
        warnings.warn("No information on phase conductors, spacing, and lengths were found in CYMOVERHEADBYPHASE for feeder_id: {:s}.".format(feederId), RuntimeWarning)
    else:
        # Add all phase conductors to the line configuration dictionary.
        for row in overheadbyphase_db:
            overheadLineConfiguration = {}
            row.DeviceNumber = _fixName(row.DeviceNumber)
            if row.DeviceNumber not in cymoverheadbyphase.keys():
                cymoverheadbyphase[row.DeviceNumber] = copy.deepcopy(CYMOVERHEADBYPHASE)
                cymoverheadbyphase[row.DeviceNumber]['name'] = row.DeviceNumber
                if row.PhaseConductorIdA != 'NONE':
                    overheadLineConfiguration['conductor_A'] = _fixName(row.PhaseConductorIdA)
                    if _fixName(row.PhaseConductorIdA) not in overheadConductors:
                        overheadConductors.append(_fixName(row.PhaseConductorIdA))
                if row.PhaseConductorIdB != 'NONE':
                    overheadLineConfiguration['conductor_B'] = _fixName(row.PhaseConductorIdB)
                    if _fixName(row.PhaseConductorIdB) not in overheadConductors:
                        overheadConductors.append(_fixName(row.PhaseConductorIdB))
                if row.PhaseConductorIdC != 'NONE':
                    overheadLineConfiguration['conductor_C'] = _fixName(row.PhaseConductorIdC)
                    if _fixName(row.PhaseConductorIdC) not in overheadConductors:
                        overheadConductors.append(_fixName(row.PhaseConductorIdC))
                if row.NeutralConductorId != 'NONE':
                    overheadLineConfiguration['conductor_N'] = _fixName(row.NeutralConductorId)
                    if row.NeutralConductorId != 'NONE' and _fixName(row.NeutralConductorId) not in overheadConductors:
                        overheadConductors.append(_fixName(row.NeutralConductorId))
                overheadLineConfiguration['spacing'] = _fixName(row.ConductorSpacingId)
                if _fixName(row.ConductorSpacingId) not in uniqueSpacing:
                    uniqueSpacing.append(_fixName(row.ConductorSpacingId))           
                cymoverheadbyphase[row.DeviceNumber]['length'] = float(row.Length)*m2ft
                if cymoverheadbyphase[row.DeviceNumber]['length'] == 0.0:
                    cymoverheadbyphase[row.DeviceNumber]['length'] = 1.0
                # if len(olc) == 0:
                #     olc['olc0'] = copy.deepcopy(overheadLineConfiguration)
                #     cymoverheadbyphase[row.DeviceNumber]['configuration'] = 'olc0'
                #
                # else:
                for key in olc.keys():
                    if overheadLineConfiguration == olc[key]:
                        cymoverheadbyphase[row.DeviceNumber]['configuration'] = key
                        if key not in olcDevice.keys():
                            olcDevice[key] = list()
                        olcDevice[key].append(row.DeviceNumber)
                        if key not in thisolc:
                            thisolc[key] = copy.deepcopy(overheadLineConfiguration)
                        break
                if cymoverheadbyphase[row.DeviceNumber]['configuration'] == None:
                    key = 'olc' + str(len(olc))
                    olc[key] = copy.deepcopy(overheadLineConfiguration)
                    cymoverheadbyphase[row.DeviceNumber]['configuration'] = key
                    if key not in olcDevice.keys():
                        olcDevice[key] = list()
                    olcDevice[key].append(row.DeviceNumber)
                    if key not in thisolc:
                        thisolc[key] = copy.deepcopy(overheadLineConfiguration)
    return overheadConductors, cymoverheadbyphase, thisolc, uniqueSpacing, olcDevice

def _readCymeUndergroundLine(networkDatabase, feederId):
    '''store information from CYMUNDERGROUNDLINE.Unique underground line 
    configuration equipment is expected to be located in CYMUNDERGROUNDLINE. The
    expected columns that are read are DeviceNumber, CableId, and Length. Length 
    is assumed to be in meters.'''
    cymundergroundline = {}                         # Stores information found in CYMUNDERGOUNDLINE in the network database
    undergroundConductors = []  # Stores the unique underground conductor equipment Ids
    CYMUNDERGROUNDLINE = { 'name' : None,           # Information structure for each object found in CYMUNDERGROUNDLINE
                           'length' : None,
                           'cable_id': None}
    ug_line_db = networkDatabase.execute("SELECT DeviceNumber, CableId, Length FROM CYMUNDERGROUNDLINE WHERE NetworkId = '{:s}'".format(feederId)).fetchall()
    if len(ug_line_db) == 0:
        warnings.warn("No underground_line objects were found in CYMUNDERGROUNDLINE for feeder_id: {:s}.".format(feederId), RuntimeWarning)
    else:
        for row in ug_line_db:
            row.DeviceNumber = _fixName(row.DeviceNumber)
            if row.DeviceNumber not in list(cymundergroundline.keys()):
                cymundergroundline[row.DeviceNumber] = copy.deepcopy(CYMUNDERGROUNDLINE)
                cymundergroundline[row.DeviceNumber]['name'] = _fixName(row.DeviceNumber)  
                cymundergroundline[row.DeviceNumber]['cable_id'] = _fixName(row.CableId)
                cymundergroundline[row.DeviceNumber]['length'] = float(row.Length)*m2ft
                if cymundergroundline[row.DeviceNumber]['length'] == 0.0:
                    cymundergroundline[row.DeviceNumber]['length'] = 1.0
                if _fixName(row.CableId) not in undergroundConductors:
                    undergroundConductors.append(_fixName(row.CableId))
    return undergroundConductors, cymundergroundline

def _readCymeOverheadLineUnbalanced(networkDatabase, feederId):
    '''store information from CYMOVERHEADLINEUNBALANCED.Unique overhead line 
    configuration equipment is expected to be located in CYMOVERHEADLINEUNBALANCED. The
    expected columns that are read are DeviceNumber, LineId, and Length. Length 
    is assumed to be in meters.'''
    cymoverheadlineunbalanced = {}                         # Stores information found in CYMOVERHEADLINEUNBALANCED in the network database
    OhUbConductors = []  # Stores the unique underground conductor equipment Ids
    CYMOVERHEADLINEUNBALANCED = { 'name' : None,           # Information structure for each object found in CYMOVERHEADLINEUNBALANCED
                           'length' : None,
                           'configuration': None}
    ug_line_db = networkDatabase.execute("SELECT DeviceNumber, LineId, Length FROM CYMOVERHEADLINEUNBALANCED WHERE NetworkId = '{:s}'".format(feederId)).fetchall()
    if len(ug_line_db) == 0:
        warnings.warn("No underground_line objects were found in CYMOVERHEADLINEUNBALANCED for feeder_id: {:s}.".format(feederId), RuntimeWarning)
    else:
        for row in ug_line_db:
            row.DeviceNumber = _fixName(row.DeviceNumber)
            if row.DeviceNumber not in list(cymoverheadlineunbalanced.keys()):
                cymoverheadlineunbalanced[row.DeviceNumber] = copy.deepcopy(CYMOVERHEADLINEUNBALANCED)
                cymoverheadlineunbalanced[row.DeviceNumber]['name'] = _fixName(row.DeviceNumber)  
                cymoverheadlineunbalanced[row.DeviceNumber]['configuration'] = _fixName(row.LineId)
                cymoverheadlineunbalanced[row.DeviceNumber]['length'] = float(row.Length)*m2ft
                if cymoverheadlineunbalanced[row.DeviceNumber]['length'] == 0.0:
                    cymoverheadlineunbalanced[row.DeviceNumber]['length'] = 1.0
                if _fixName(row.LineId) not in OhUbConductors:
                    OhUbConductors.append(_fixName(row.LineId))
    return cymoverheadlineunbalanced, OhUbConductors

def _readCymeSection(networkDatabase, feederId):
    '''store information from CYMSECTION. Information on all link type 
    devices are expected to be stored here. The read columns are SectionId, 
    FromNodeId, ToNodeId, and Phase.'''
    cymsection = {}                         # Stores information found in CYMSECTION in the network database
    CYMSECTION = {  'name' : None,           # Information structure for each object found in CYMSECTION
                                   'from' : None,
                                   'to' : None,
                                   'phases' : None}
    section_db = networkDatabase.execute("SELECT SectionId, FromNodeId, ToNodeId, Phase FROM CYMSECTION WHERE NetworkId = '{:s}'".format(feederId)).fetchall()
    if len(section_db) == 0:
        warnings.warn("No section information was found in CYMSECTION for feeder_id: {:s}.".format(feederId), RuntimeWarning)
    else:
        for row in section_db:
            row.SectionId = _fixName(row.SectionId)
            if row.SectionId not in list(cymsection.keys()):
                cymsection[row.SectionId] = copy.deepcopy(CYMSECTION)
                cymsection[row.SectionId]['name'] = row.SectionId            
                cymsection[row.SectionId]['from'] = _fixName(row.FromNodeId)
                cymsection[row.SectionId]['to'] = _fixName(row.ToNodeId)
                cymsection[row.SectionId]['phases'] = _convertPhase(int(row.Phase))
    return cymsection

def _readCymeSectionDevice(networkDatabase, feederId):
    '''store information from CYMSECTIONDEVICE. Information on all link type 
    devices are expected to be stored here. The read columns are DeviceNumber, 
    DeviceType, SectionId, and Location.'''
    cymsectiondevice = {}                         # Stores information found in CYMSECTIONDEVICE in the network database
    CYMSECTIONDEVICE = { 'name' : None,           # Information structure for each object found in CYMSECTIONDEVICE
                        'device_type' : None,
                        'section_name' : None,
                        'location' : None}
    section_device_db = networkDatabase.execute("SELECT DeviceNumber, DeviceType, SectionId, Location FROM CYMSECTIONDEVICE WHERE NetworkId = '{:s}'".format(feederId)).fetchall()
    if len(section_device_db) == 0:
        warnings.warn("No section device information was found in CYMSECTIONDEVICE for feeder_id: {:s}.".format(feederId), RuntimeWarning)
    else:
        for row in section_device_db:
            row.SectionId = _fixName(row.SectionId)
            row.DeviceNumber = _fixName(row.DeviceNumber)
            if row.DeviceNumber not in cymsectiondevice.keys():
                cymsectiondevice[row.DeviceNumber] = copy.deepcopy(CYMSECTIONDEVICE)
                cymsectiondevice[row.DeviceNumber]['name'] = row.DeviceNumber             
                cymsectiondevice[row.DeviceNumber]['device_type'] = int(row.DeviceType)
                cymsectiondevice[row.DeviceNumber]['section_name'] = row.SectionId
                cymsectiondevice[row.DeviceNumber]['location'] = int(row.Location)
    return cymsectiondevice

def _splitLinkObjects(sectionDict, deviceDict, linkDict, overheadDict, undergroundDict):
    '''Split multiple link objects from the line that they are folded into'''
    for link in linkDict.keys():
        if link in overheadDict.keys() or link in undergroundDict.keys(): # if true the link is embedded in a line object and must be separated
            lineId = link
            newLinkId = linkDict[link]
            if deviceDict[newLinkId]['location'] == 1: # device is at the from side of a section
                sectionDict[newLinkId] = copy.deepcopy(sectionDict[lineId])
                sectionDict[newLinkId]['name'] = newLinkId
                sectionDict[newLinkId]['to'] = 'node' + newLinkId
                sectionDict[newLinkId]['toX'] = str(float(sectionDict[lineId]['fromX']) + random.uniform(-10,10))
                sectionDict[newLinkId]['toY'] = str(float(sectionDict[lineId]['fromY']) + random.uniform(-10,10))
                sectionDict[lineId]['from'] = 'node' + newLinkId
                sectionDict[lineId]['fromX'] = sectionDict[newLinkId]['toX']
                sectionDict[lineId]['fromY'] = sectionDict[newLinkId]['toY']
            else: # device is at the to side of a section
                sectionDict[newLinkId] = copy.deepcopy(sectionDict[lineId])
                sectionDict[newLinkId]['name'] = newLinkId
                sectionDict[newLinkId]['from'] = 'node' + newLinkId
                sectionDict[newLinkId]['fromX'] = str(float(sectionDict[lineId]['toX']) + random.uniform(-10,10))
                sectionDict[newLinkId]['fromY'] = str(float(sectionDict[lineId]['toY']) + random.uniform(-10,10))
                sectionDict[lineId]['to'] = 'node' + newLinkId
                sectionDict[lineId]['toX'] = sectionDict[newLinkId]['fromX']
                sectionDict[lineId]['toY'] = sectionDict[newLinkId]['fromY']
            for phase in ['N', 'D']:
                sectionDict[newLinkId]['phases'] = sectionDict[newLinkId]['phases'].replace(phase, '')
            deviceDict[newLinkId]['section_name'] = newLinkId
            deviceDict[newLinkId]['location'] = 0

def _findParents(sectionDict, deviceDict, loadDict):
    '''store parent information for load type objects'''
    for loadsection in list(loadDict.keys()):
        lineId = loadsection
        loaddevice = loadDict[lineId]
        if deviceDict[loaddevice]['location'] == 2:
            deviceDict[loaddevice]['parent'] = sectionDict[lineId]['to']
        else:
            deviceDict[loaddevice]['parent'] = sectionDict[lineId]['from']
        deviceDict[loaddevice]['phases'] = sectionDict[lineId]['phases']

def _readCymeSwitch(networkDatabase, feederId):
    '''store information from CYMSWITCH. Information on all switch type 
    devices are expected to be stored here. The read columns are DeviceNumber, 
    EquipmentId, and ClosedPhase.'''
    cymswitch = {}                          # Stores information found in CYMSWITCH in the network database
    CYMSWITCH = { 'name' : None,            # Information structure for each object found in CYMSWITCH
                  'equipment_name' : None,
                  'status' : None}
    switch_db = networkDatabase.execute("SELECT DeviceNumber, EquipmentId, ClosedPhase FROM CYMSWITCH WHERE NetworkId = '{:s}'".format(feederId)).fetchall()
    if len(switch_db) == 0:
        warnings.warn("No switch objects were found in CYMSWITCH for feeder_id: {:s}.".format(feederId), RuntimeWarning)
    else:
        for row in switch_db:
            row.DeviceNumber = _fixName(row.DeviceNumber)
            row.EquipmentId = _fixName(row.EquipmentId)
            if row.DeviceNumber not in list(cymswitch.keys()):
                cymswitch[row.DeviceNumber] = copy.deepcopy(CYMSWITCH)
                cymswitch[row.DeviceNumber]['name'] = row.DeviceNumber             
                cymswitch[row.DeviceNumber]['equipment_name'] = row.EquipmentId
                if float(row.ClosedPhase) == 0.0:
                    cymswitch[row.DeviceNumber]['status'] = 0
                else:
                    cymswitch[row.DeviceNumber]['status'] = 1
    return cymswitch

def _readCymeSectionalizer(networkDatabase, feederId):
    '''store information from CYMSECTIONALIZER. Information on all sectionalizer type 
    devices are expected to be stored here. The read columns are DeviceNumber 
    and NormalStatus.'''
    cymsectionalizer = {}                           # Stores information found in CYMSECTIONALIZER in the network database
    CYMSECTIONALIZER = { 'name' : None,             # Information structure for each object found in CYMSECTIONALIZER
                         'status' : None}                 
    sectionalizer_db = networkDatabase.execute("SELECT DeviceNumber, NormalStatus FROM CYMSECTIONALIZER WHERE NetworkId = '{:s}'".format(feederId)).fetchall()
    if len(sectionalizer_db) == 0:
        warnings.warn("No sectionalizer objects were found in CYMSECTIONALIZER for feeder_id: {:s}.".format(feederId),RuntimeWarning)
    else:
        for row in sectionalizer_db:
            row.DeviceNumber = _fixName(row.DeviceNumber)
            if row.DeviceNumber not in list(cymsectionalizer.keys()):
                cymsectionalizer[row.DeviceNumber] = copy.deepcopy(CYMSECTIONALIZER)
                cymsectionalizer[row.DeviceNumber]['name'] = row.DeviceNumber
                if float(row.NormalStatus) == 0:
                    cymsectionalizer[row.DeviceNumber]['status'] = 0
                else:
                    cymsectionalizer[row.DeviceNumber]['status'] = 1
    return cymsectionalizer

def _readCymeFuse(networkDatabase, feederId):
    '''store information from CYMFUSE. Information on all fuse type 
    devices are expected to be stored here. The read columns are DeviceNumber, 
    EquipmentId, and NormalStatus.'''
    cymfuse = {}                           # Stores information found in CYMFUSE in the network database
    CYMFUSE = { 'name' : None,             # Information structure for each object found in CYMFUSE
                'status' : None,
                'equipment_id' : None}
    fuse_db = networkDatabase.execute("SELECT DeviceNumber, EquipmentId, NormalStatus FROM CYMFUSE WHERE NetworkId = '{:s}'".format(feederId)).fetchall()
    if len(fuse_db) == 0:
        warnings.warn("No fuse objects were found in CYMFUSE for feeder_id: {:s}.".format(feederId), RuntimeWarning)
    else:
        for row in fuse_db:
            row.DeviceNumber = _fixName(row.DeviceNumber)
            row.EquipmentId = _fixName(row.EquipmentId)
            if row.DeviceNumber not in list(cymfuse.keys()):
                cymfuse[row.DeviceNumber] = copy.deepcopy(CYMFUSE)
                cymfuse[row.DeviceNumber]['name'] = row.DeviceNumber
                cymfuse[row.DeviceNumber]['equipment_id'] = row.EquipmentId
                if float(row.NormalStatus) == 0:
                    cymfuse[row.DeviceNumber]['status'] = 0
                else:
                    cymfuse[row.DeviceNumber]['status'] = 1
    return cymfuse

def _readCymeRecloser(networkDatabase, feederId):
    '''store information from CYMRECLOSER. Information on all recloser type 
    devices are expected to be stored here. The read columns are DeviceNumber 
    and NormalStatus.'''
    cymrecloser = {}
    CYMRECLOSER = {    'name' : None,
                    'status' : None}
    recloser_db = networkDatabase.execute("SELECT DeviceNumber, NormalStatus FROM CYMRECLOSER WHERE NetworkId = '{:s}'".format(feederId)).fetchall()
    if len(recloser_db) == 0:
        warnings.warn("No recloser objects were found in CYMRECLOSER for feeder_id: {:s}.".format(feederId), RuntimeWarning)
    else:
        for row in recloser_db:
            row.DeviceNumber = _fixName(row.DeviceNumber)
            if row.DeviceNumber not in list(cymrecloser.keys()):
                cymrecloser[row.DeviceNumber] = copy.deepcopy(CYMRECLOSER)
                cymrecloser[row.DeviceNumber]['name'] = row.DeviceNumber
                if float(row.NormalStatus) == 0:
                    cymrecloser[row.DeviceNumber]['status'] = 0
                else:
                    cymrecloser[row.DeviceNumber]['status'] = 1
    return cymrecloser

def _readCymeRegulator(networkDatabase, feederId):
    '''store information from CYMREGULATOR. Information on all regulator type 
    devices are expected to be stored here. The read columns are DeviceNumber, 
    EquipmentId, BandWidth, BoostPercent, TapPositionA, TapPositionB, 
    and TapPositionC.'''
    cymregulator = {}                           # Stores information found in CYMREGULATOR in the network database
    CYMREGULATOR = { 'name' : None,             # Information structure for each object found in CYMREGULATOR
                     'equipment_name' : None,
                     'regulation' : None,
                     'band_width' : None,
                     'tap_pos_A' : None,
                     'tap_pos_B' : None,
                     'tap_pos_C' : None}
    #regulator_db = networkDatabase.execute("SELECT DeviceNumber, EquipmentId, BandWidth, BoostPercent, TapPositionA, TapPositionB, TapPositionC FROM CYMREGULATOR WHERE NetworkId = '{:s}'".format(feederId)).fetchall()
    regulator_db = networkDatabase.execute("SELECT DeviceNumber, EquipmentId, BoostPercent, TapPositionA, TapPositionB, TapPositionC FROM CYMREGULATOR WHERE NetworkId = '{:s}'".format(feederId)).fetchall()
    if len(regulator_db) == 0:
        warnings.warn("No regulator objects were found in CYMREGULATOR for feeder_id: {:s}".format(feederId), RuntimeWarning)
    else:
        for row in regulator_db:
            row.DeviceNumber = _fixName(row.DeviceNumber)
            row.EquipmentId = _fixName(row.EquipmentId)
            if row.DeviceNumber not in list(cymregulator.keys()):
                cymregulator[row.DeviceNumber] = copy.deepcopy(CYMREGULATOR)
                cymregulator[row.DeviceNumber]['name'] = row.DeviceNumber          
                cymregulator[row.DeviceNumber]['equipment_name'] = row.EquipmentId
                #cymregulator[row.DeviceNumber]['band_width'] = float(row.BandWidth)/120.0
                cymregulator[row.DeviceNumber]['regulation'] = float(row.BoostPercent)/100.0
                cymregulator[row.DeviceNumber]['tap_pos_A'] = row.TapPositionA
                cymregulator[row.DeviceNumber]['tap_pos_B'] = row.TapPositionB
                cymregulator[row.DeviceNumber]['tap_pos_C'] = row.TapPositionC
    return cymregulator

def _readCymeShuntCapacitor(networkDatabase, feederId, type):
    '''store information from CYMSHUNTCAPACITOR. Information on all shunt capacitory 
    type devices are expected to be stored here. The read columns are 
    DeviceNumber, EquipmentId, Status, Phase, KVARA, KVARB, KVARC, KVLN, 
    CapacitorControlType, OnValue, OffValue, KVARABC or DeviceNumber, NetworkId, 
    EquipmentId, Status, KVARA, KVARB, KVARC, KVLN, CapacitorControlType, 
    OnValueA, and OffValueA.'''
    cymshuntcapacitor = {}                           # Stores information found in CYMSHUNTCAPACITOR in the network database
    if (type==1):
        CYMSHUNTCAPACITOR = { 'name' : None,             # Information structure for each object found in CYMSHUNTCAPACITOR
                              'equipment_name' : None,
                              'status' : None,
                              'phases' : None,
                              'capacitor_A' : None,
                              'capacitor_B' : None,
                              'capacitor_C' : None,
                              'capacitor_ABC' : None,
                              'kv_line_neutral' : None,
                              'control' : None,
                              'voltage_set_high' : None,
                              'voltage_set_low' : None,
                              'VAr_set_high' : None,
                              'VAr_set_low' : None,
                              'current_set_high' : None,
                              'current_set_low' : None,
                              'pt_phase' : None}
        try:
            shuntcapacitor_db = networkDatabase.execute("SELECT DeviceNumber, EquipmentId, Status, Phase, KVARA, KVARB, KVARC, KVLN, CapacitorControlType, OnValue, OffValue, KVARABC, SwitchedKVARA, SwitchedKVARB, SwitchedKVARC FROM CYMSHUNTCAPACITOR WHERE NetworkId = '{:s}'".format(feederId)).fetchall()
        except:
            shuntcapacitor_db = networkDatabase.execute("SELECT DeviceNumber, EquipmentId, Status,  KVARA, KVARB, KVARC, KVLN, CapacitorControlType, OnValueA, OffValueA, SwitchedKVARA, SwitchedKVARB, SwitchedKVARC FROM CYMSHUNTCAPACITOR WHERE NetworkId = '{:s}'".format(feederId)).fetchall()
    elif (type==2):
        CYMSHUNTCAPACITOR = { 'name' : None,             # Information structure for each object found in CYMSHUNTCAPACITOR
                              'equipment_name' : None,
                              'status' : None,
                              'phases' : None,
                              'capacitor_A' : None,
                              'capacitor_B' : None,
                              'capacitor_C' : None,
                              'capacitor_ABC' : None,
                              'kv_line_neutral' : None,
                              'control' : None,
                              'voltage_set_high' : None,
                              'voltage_set_low' : None,
                              'VAr_set_high' : None,
                              'VAr_set_low' : None,
                              'current_set_high' : None,
                              'current_set_low' : None,
                              'pt_phase' : None}
                              
        shuntcapacitor_db = networkDatabase.execute("SELECT DeviceNumber, NetworkId, EquipmentId, Status, KVARA, KVARB, KVARC, KVLN, CapacitorControlType, OnValueA, OffValueA FROM CYMSHUNTCAPACITOR WHERE NetworkId = '{:s}'".format(feederId)).fetchall()        
    if len(shuntcapacitor_db) == 0:
        warnings.warn("No capacitor objects were found in CYMSHUNTCAPACITOR for feeder_id: {:s}".format(feederId), RuntimeWarning)
    else:
        for row in shuntcapacitor_db:
            row.DeviceNumber = _fixName(row.DeviceNumber)
            if row.EquipmentId is None:
                row.EquipmentId = 'DEFAULT'
            row.EquipmentId = _fixName(row.EquipmentId)
            if row.DeviceNumber not in cymshuntcapacitor.keys():
                cymshuntcapacitor[row.DeviceNumber] = copy.deepcopy(CYMSHUNTCAPACITOR)
                cymshuntcapacitor[row.DeviceNumber]['name'] = row.DeviceNumber
                cymshuntcapacitor[row.DeviceNumber]['equipment_name'] = row.EquipmentId
                cymshuntcapacitor[row.DeviceNumber]['phases'] = "ABCN"
                cymshuntcapacitor[row.DeviceNumber]['status'] = row.Status
                if float(row.KVARA) > 0.0:
                    cymshuntcapacitor[row.DeviceNumber]['capacitor_A'] = float(row.KVARA)*1000
                if float(row.KVARB) > 0.0:
                    cymshuntcapacitor[row.DeviceNumber]['capacitor_B'] = float(row.KVARB)*1000
                if float(row.KVARC) > 0.0:
                    cymshuntcapacitor[row.DeviceNumber]['capacitor_C'] = float(row.KVARC)*1000
                if float(row.KVLN) > 0.0:
                    cymshuntcapacitor[row.DeviceNumber]['kV_line_neutral'] = float(row.KVLN)*1000
                if int(row.CapacitorControlType) == 2:
                    cymshuntcapacitor[row.DeviceNumber]['control'] = 'VAR'
                    cymshuntcapacitor[row.DeviceNumber]['VAr_set_high'] = float(row.OnValueA)*1000
                    cymshuntcapacitor[row.DeviceNumber]['VAr_set_low'] = float(row.OffValueA)*1000
                    cymshuntcapacitor[row.DeviceNumber]['pt_phase'] = "ABCN"
                    cymshuntcapacitor[row.DeviceNumber]['capacitor_A'] = float(row.SwitchedKVARA)*1000
                    cymshuntcapacitor[row.DeviceNumber]['capacitor_B'] = float(row.SwitchedKVARB)*1000
                    cymshuntcapacitor[row.DeviceNumber]['capacitor_C'] = float(row.SwitchedKVARC)*1000
                elif int(row.CapacitorControlType) == 3:
                    cymshuntcapacitor[row.DeviceNumber]['control'] = 'CURRENT'
                    cymshuntcapacitor[row.DeviceNumber]['current_set_high'] = row.OnValueA
                    cymshuntcapacitor[row.DeviceNumber]['current_set_low'] = row.OffValueA
                    cymshuntcapacitor[row.DeviceNumber]['pt_phase'] = "ABCN"
                elif int(row.CapacitorControlType) == 7:
                    cymshuntcapacitor[row.DeviceNumber]['control'] = 'VOLT'
                    cymshuntcapacitor[row.DeviceNumber]['voltage_set_high'] = row.OnValueA
                    cymshuntcapacitor[row.DeviceNumber]['voltage_set_low'] = row.OffValueA
                    cymshuntcapacitor[row.DeviceNumber]['pt_phase'] = "ABCN"
                else:
                    cymshuntcapacitor[row.DeviceNumber]['control'] = 'MANUAL'
                    cymshuntcapacitor[row.DeviceNumber]['pt_phase'] = "ABCN"
                    cymshuntcapacitor[row.DeviceNumber]['voltage_set_high'] = float(row.KVLN)*1000
                    cymshuntcapacitor[row.DeviceNumber]['voltage_set_low'] = float(row.KVLN)*1000
    return cymshuntcapacitor

def _determineLoad( l_type, l_v1, l_v2, conKVA):
    l_real = 0
    l_imag = 0
    if l_type == 0: # information was stored as kW & kVAR
        l_real = l_v1 * 1000.0
        l_imag = abs(l_v2) * 1000.0
    elif l_type == 1: # information was stored as kVA & power factor
        l_real = l_v1 * abs(l_v2)/100.0 * 1000.0
        l_imag = l_v1 * math.sqrt(1 - (abs(l_v2)/100.0)**2) * 1000.0
    else: # information was stored as kW and power factor
        l_real = l_v1 * 1000.0
        if l_v2 != 0.0:
            l_imag = l_real/(abs(l_v2)/100.0)*math.sqrt(1-(abs(l_v2)/100.0)**2)
    # Do not use conKVA to calculate results when type is 1, table CYMCUSTOMERLOAD, column LoadValueType, LoadValue1, LoadValue2, ConnectedKVA
    # if l_real == 0.0 and l_imag == 0.0:
    #         l_real = conKVA * abs(l_v2)/100.0 * 1000.0
    #         l_imag = conKVA * math.sqrt(1 - (abs(l_v2)/100.0)**2) * 1000.0
    if l_v2 < 0.0:
        l_imag *= -1.0
    return [l_real, l_imag]

def _setConstantPower(l_v2, l_real, l_imag):
    if l_v2 >= 0.0:
        cp_string = '{:0.3f}+{:0.3f}j'.format(l_real,l_imag)

    else:
        cp_string = '{:0.3f}-{:0.3f}j'.format(l_real,abs(l_imag))

    return cp_string

def _cleanPhases(phases):
    p = ''
    if 'A' in phases:
        p = p + 'A'
    if 'B' in phases:
        p = p + 'B'
    if 'C' in phases:
        p = p + 'C'
    return p

def _readCymeCustomerLoad(networkDatabase, feederId):
    '''store information from CYMCUSTOMERLOAD. Information on all customer load 
    type devices are expected to be stored here. The read columns are 
    DeviceNumber, DeviceType, ConsumerClassId, Phase, LoadValueType, Phase, 
    LoadValue1, LoadValue2, and ConnectedKVA.'''
    cymcustomerload = {}                           # Stores information found in CYMCUSTOMERLOAD in the network database
    CYMCUSTOMERLOAD = { 'name' : None,             # Information structure for each object found in CYMCUSTERLOAD
                      'phases' : None,
                      'constant_power_A' : None,
                      'constant_power_B' : None,
                      'constant_power_C' : None,
                      'load_realA' : 0.0,
                      'load_imagA' : 0.0,
                      'load_realB' : 0.0,
                      'load_imagB' : 0.0,
                      'load_realC' : 0.0,
                      'load_imagC' : 0.0,
                      'load_class' : None}
    load_real = 0
    load_imag = 0
    customerload_db = networkDatabase.execute("SELECT DeviceNumber, DeviceType, ConsumerClassId, Phase, LoadValueType, Phase, LoadValue1, LoadValue2, ConnectedKVA FROM CYMCUSTOMERLOAD WHERE NetworkId = '{:s}'".format(feederId)).fetchall()
    if len(customerload_db) == 0:
        warnings.warn("No load objects were found in CYMCUSTOMERLOAD for feeder_id: {:s}.".format(feederId), RuntimeWarning)
    else:
        for row in customerload_db:
            row.DeviceNumber = _fixName(row.DeviceNumber)
            if row.DeviceNumber not in cymcustomerload.keys():
                # check for 0 load
                [load_real, load_imag] = _determineLoad(int(row.LoadValueType), float(row.LoadValue1), float(row.LoadValue2), row.ConnectedKVA)
                cymcustomerload[row.DeviceNumber] = copy.deepcopy(CYMCUSTOMERLOAD)
                cymcustomerload[row.DeviceNumber]['name'] = row.DeviceNumber            
                cymcustomerload[row.DeviceNumber]['phases'] = _cleanPhases(_convertPhase(int(row.Phase)))
                # Determine the load classification
                if 'residential' in (row.ConsumerClassId).lower():
                    cymcustomerload[row.DeviceNumber]['load_class'] = 'R'
                elif 'commercial' in (row.ConsumerClassId).lower():
                    cymcustomerload[row.DeviceNumber]['load_class'] = 'C'
                else:
                    # cymcustomerload[row.DeviceNumber]['load_class'] = 'R'
                    cymcustomerload[row.DeviceNumber]['load_class'] = 'C'
                convert_class = _convertLoadClass(row.ConsumerClassId)
                if convert_class is not None:
                    cymcustomerload[row.DeviceNumber]['load_class'] = convert_class
                
                if int(row.Phase) == 1:
                    cymcustomerload[row.DeviceNumber]['load_realA'] = load_real
                    cymcustomerload[row.DeviceNumber]['load_imagA'] = load_imag
                    cymcustomerload[row.DeviceNumber]['constant_power_A'] = _setConstantPower(float(row.LoadValue2), load_real, load_imag)
                elif int(row.Phase) == 2:
                    cymcustomerload[row.DeviceNumber]['load_realB'] = load_real
                    cymcustomerload[row.DeviceNumber]['load_imagB'] = load_imag
                    cymcustomerload[row.DeviceNumber]['constant_power_B'] = _setConstantPower(float(row.LoadValue2), load_real, load_imag)
                elif int(row.Phase) == 3:
                    cymcustomerload[row.DeviceNumber]['load_realC'] = load_real
                    cymcustomerload[row.DeviceNumber]['load_imagC'] = load_imag
                    cymcustomerload[row.DeviceNumber]['constant_power_C'] = _setConstantPower(float(row.LoadValue2), load_real, load_imag)
                elif int(row.Phase) == 7:
                    cymcustomerload[row.DeviceNumber]['load_realA'] = load_real/3.0
                    cymcustomerload[row.DeviceNumber]['load_imagA'] = load_imag/3.0
                    cymcustomerload[row.DeviceNumber]['constant_power_A'] = _setConstantPower(float(row.LoadValue2), load_real/3.0, load_imag/3.0)
                    cymcustomerload[row.DeviceNumber]['load_realB'] = load_real/3.0
                    cymcustomerload[row.DeviceNumber]['load_imagB'] = load_imag/3.0
                    cymcustomerload[row.DeviceNumber]['constant_power_B'] = _setConstantPower(float(row.LoadValue2), load_real/3.0, load_imag/3.0)
                    cymcustomerload[row.DeviceNumber]['load_realC'] = load_real/3.0
                    cymcustomerload[row.DeviceNumber]['load_imagC'] = load_imag/3.0
                    cymcustomerload[row.DeviceNumber]['constant_power_C'] = _setConstantPower(float(row.LoadValue2), load_real/3.0, load_imag/3.0)                    
            else:
                [load_real, load_imag] = _determineLoad(int(row.LoadValueType), float(row.LoadValue1), float(row.LoadValue2), float(row.ConnectedKVA))
                ph = cymcustomerload[row.DeviceNumber]['phases'] + _convertPhase(int(row.Phase))
                cymcustomerload[row.DeviceNumber]['phases'] = _cleanPhases(ph)
                if int(row.Phase) == 1:
                    cymcustomerload[row.DeviceNumber]['load_realA'] += load_real
                    cymcustomerload[row.DeviceNumber]['load_imagA'] += load_imag
                    cymcustomerload[row.DeviceNumber]['constant_power_A'] = _setConstantPower(float(row.LoadValue2), cymcustomerload[row.DeviceNumber]['load_realA'], cymcustomerload[row.DeviceNumber]['load_imagA'])	
                elif int(row.Phase) == 2:
                    cymcustomerload[row.DeviceNumber]['load_realB'] += load_real
                    cymcustomerload[row.DeviceNumber]['load_imagB'] += load_imag
                    cymcustomerload[row.DeviceNumber]['constant_power_B'] = _setConstantPower(float(row.LoadValue2), cymcustomerload[row.DeviceNumber]['load_realB'], cymcustomerload[row.DeviceNumber]['load_imagB']) 
                elif int(row.Phase) == 3:
                    cymcustomerload[row.DeviceNumber]['load_realC'] += load_real
                    cymcustomerload[row.DeviceNumber]['load_imagC'] += load_imag
                    cymcustomerload[row.DeviceNumber]['constant_power_C'] = _setConstantPower(float(row.LoadValue2), cymcustomerload[row.DeviceNumber]['load_realC'], cymcustomerload[row.DeviceNumber]['load_imagC'])

                elif int(row.Phase) == 7:
                    cymcustomerload[row.DeviceNumber]['load_realA'] += load_real/3.0
                    cymcustomerload[row.DeviceNumber]['load_imagA'] += load_imag/3.0
                    cymcustomerload[row.DeviceNumber]['constant_power_A'] = _setConstantPower(float(row.LoadValue2), cymcustomerload[row.DeviceNumber]['load_realA'], cymcustomerload[row.DeviceNumber]['load_imagA'])
                    cymcustomerload[row.DeviceNumber]['load_realB'] += load_real/3.0
                    cymcustomerload[row.DeviceNumber]['load_imagB'] += load_imag/3.0
                    cymcustomerload[row.DeviceNumber]['constant_power_B'] = _setConstantPower(float(row.LoadValue2), cymcustomerload[row.DeviceNumber]['load_realB'], cymcustomerload[row.DeviceNumber]['load_imagB'])
                    cymcustomerload[row.DeviceNumber]['load_realC'] += load_real/3.0
                    cymcustomerload[row.DeviceNumber]['load_imagC'] += load_imag/3.0
                    cymcustomerload[row.DeviceNumber]['constant_power_C'] = _setConstantPower(float(row.LoadValue2), cymcustomerload[row.DeviceNumber]['load_realC'], cymcustomerload[row.DeviceNumber]['load_imagC'])    
            #print cymcustomerload[row.DeviceNumber]['constant_power_C']
            #print cymcustomerload[row.DeviceNumber]['constant_power_B']
            #print cymcustomerload[row.DeviceNumber]['constant_power_A']

    return cymcustomerload

def _readCymeThreeWindingTransformer(networkDatabase, feederId):
    '''store information from CYMTHREEWINDINGTRANSFORMER. Information on some 
    transformer type devices are expected to be stored here. The read columns 
    are DeviceNumber, EquipmentId.'''
    cymthreewxfmr = {}                           # Stores information found in CYMREGULATOR in the network database
    CYMTHREEWXFMR = { 'name' : None,             # Information structure for each object found in CYMREGULATOR
                     'equipment_name' : None}                 
    threewxfmr_db = networkDatabase.execute("SELECT DeviceNumber, EquipmentId FROM CYMTHREEWINDINGTRANSFORMER WHERE NetworkId = '{:s}'".format(feederId)).fetchall()
    if len(threewxfmr_db) == 0:
        warnings.warn("No three-winding transformer objects were found in CYMTHREEWINDINGTRANSFORMER for feeder_id: {:s}".format(feederId), RuntimeWarning)
    else:
        for row in threewxfmr_db:
            row.DeviceNumber = _fixName(row.DeviceNumber)
            row.EquipmentId = _fixName(row.EquipmentId)
            if row.DeviceNumber not in list(cymthreewxfmr.keys()):
                cymthreewxfmr[row.DeviceNumber] = copy.deepcopy(CYMTHREEWXFMR)
                cymthreewxfmr[row.DeviceNumber]['name'] = row.DeviceNumber           
                cymthreewxfmr[row.DeviceNumber]['equipment_name'] = row.EquipmentId
    return cymthreewxfmr

def _readCymeTransformer(networkDatabase, feederId):
    '''store information from CYMTRANSFORMER. Information on some 
    transformer type devices are expected to be stored here. The read columns 
    are DeviceNumber, EquipmentId.'''
    cymxfmr = {}
    CYMXFMR = { 'name' : None,
               'equipment_name' : None}
    xfmrDb = networkDatabase.execute("SELECT DeviceNumber, EquipmentId FROM CYMTRANSFORMER WHERE NetworkId = '{:s}'".format(feederId)).fetchall()
    if len(xfmrDb) == 0:
        warnings.warn("No transformer objects were found in CYMTRANSFORMER for feeder id: {:s}".format(feederId), RuntimeWarning)
    else:
        for row in xfmrDb:
            row.DeviceNumber = _fixName(row.DeviceNumber)
            row.EquipmentId = _fixName(row.EquipmentId)
            if row.DeviceNumber not in list(cymxfmr.keys()):
                cymxfmr[row.DeviceNumber] = copy.deepcopy(CYMXFMR)
                cymxfmr[row.DeviceNumber]['name'] = row.DeviceNumber
                cymxfmr[row.DeviceNumber]['equipment_name'] = row.EquipmentId
    return cymxfmr

def _readEqConductor(equipmentDatabase, feederId):
    '''store information from CYMEQCONDUCTOR. Information on conductor equipmentF
    are expected to be stored here. The read columns are EquipmentId, 
    FirstRating, GMR, and R50. GMR is expected to be in cm. R50 is expected to 
    be in Ohm/km.'''
    cymeqconductor = {}                           # Stores information found in CYMEQCONDUCTOR in the equipment database
    CYMEQCONDUCTOR = { 'name' : None,             # Information structure for each object found in CYMEQCONDUCTOR
                       'rating.summer_continuous' : None,
                       'geometric_mean_radius' : None,
                       'resistance' : None}
    cymeqconductor_db = equipmentDatabase.execute("SELECT EquipmentId, FirstRating, GMR, R25 FROM CYMEQCONDUCTOR").fetchall()
    if len(cymeqconductor_db) == 0:
        warnings.warn("No conductor objects were found in CYMEQCONDUCTOR for feeder_id: {:s}.".format(feederId), RuntimeWarning)
    else:
        for row in cymeqconductor_db:
            row.EquipmentId = _fixName(row.EquipmentId)
            if row.EquipmentId not in list(cymeqconductor.keys()):
                cymeqconductor[row.EquipmentId] = copy.deepcopy(CYMEQCONDUCTOR)
                cymeqconductor[row.EquipmentId]['name'] = row.EquipmentId               
                cymeqconductor[row.EquipmentId]['rating.summer_continuous'] = row.FirstRating
                cymeqconductor[row.EquipmentId]['geometric_mean_radius'] = float(row.GMR)*m2ft/100 #GMR is stored in cm. Must convert to ft.
                cymeqconductor[row.EquipmentId]['resistance'] = float(row.R25)*5280/(m2ft*1000) # R50 is stored in Ohm/km. Must convert to Ohm/mile
    return cymeqconductor

def _readEqOverheadLineUnbalanced(networkDatabase, feederId):
    '''store information from CYMEQOVERHEADLINEUNBALANCED. The read columns are 
    EquipmentId, SelfResistanceA, SelfResistanceB, SelfResistanceC, 
    SelfReactanceA, SelfReactanceB, SelfReactanceC, MutualResistanceAB, 
    MutualResistanceBC, MutualResistanceCA, MutualReactanceAB, 
    MutualReactanceBC, and MutualReactanceCA. all values are assumed to be in 
    Ohm/km.'''
    cymeqoverheadlineunbalanced = {}                         # Stores information found in CYMEQOVERHEADLINEUNBALANCED in the network database
    CYMEQOVERHEADLINEUNBALANCED = { 'object' : 'line_configuration',
                                                                            'name' : None,
                                                                            'z11' : None,
                                                                            'z12' : None,
                                                                            'z13' : None,
                                                                            'z21' : None,
                                                                            'z22' : None,
                                                                            'z23' : None,
                                                                            'z31' : None,
                                                                            'z32' : None,
                                                                            'z33' : None}
    ug_line_db = networkDatabase.execute("SELECT EquipmentId, SelfResistanceA, SelfResistanceB, SelfResistanceC, SelfReactanceA, SelfReactanceB, SelfReactanceC, MutualResistanceAB, MutualResistanceBC, MutualResistanceCA, MutualReactanceAB, MutualReactanceBC, MutualReactanceCA FROM CYMEQOVERHEADLINEUNBALANCED WHERE EquipmentId = '{:s}'".format("LINE606")).fetchall()
    if len(ug_line_db) == 0:
        warnings.warn("No underground_line configuration objects were found in CYMEQOVERHEADLINEUNBALANCED for feeder_id: {:s}.".format(feederId), RuntimeWarning)
    else:
        for row in ug_line_db:
            row.EquipmentId = _fixName(row.EquipmentId)
            if row.EquipmentId not in cymeqoverheadlineunbalanced.keys():
                cymeqoverheadlineunbalanced[row.EquipmentId] = copy.deepcopy(CYMEQOVERHEADLINEUNBALANCED)
                cymeqoverheadlineunbalanced[row.EquipmentId]['name'] = _fixName(row.EquipmentId)  
                cymeqoverheadlineunbalanced[row.EquipmentId]['z11'] ='{:0.6f}{:+0.6}j'.format(float(row.SelfResistanceA)*5280/(m2ft*1000), float(row.SelfReactanceA)*5280/(m2ft*1000)) #  Ohm/km. Must convert to Ohm/mile
                cymeqoverheadlineunbalanced[row.EquipmentId]['z22'] ='{:0.6f}{:+0.6}j'.format(float(row.SelfResistanceB)*5280/(m2ft*1000), float(row.SelfReactanceB)*5280/(m2ft*1000)) 
                cymeqoverheadlineunbalanced[row.EquipmentId]['z33'] ='{:0.6f}{:+0.6}j'.format(float(row.SelfResistanceC)*5280/(m2ft*1000), float(row.SelfReactanceC)*5280/(m2ft*1000))
                cymeqoverheadlineunbalanced[row.EquipmentId]['z12'] ='{:0.6f}{:+0.6}j'.format(float(row.MutualResistanceAB)*5280/(m2ft*1000), float(row.MutualReactanceAB)*5280/(m2ft*1000))
                cymeqoverheadlineunbalanced[row.EquipmentId]['z21'] ='{:0.6f}{:+0.6}j'.format(float(row.MutualResistanceAB)*5280/(m2ft*1000), float(row.MutualReactanceAB)*5280/(m2ft*1000))  
                cymeqoverheadlineunbalanced[row.EquipmentId]['z23'] ='{:0.6f}{:+0.6}j'.format(float(row.MutualResistanceBC)*5280/(m2ft*1000), float(row.MutualReactanceBC)*5280/(m2ft*1000))  
                cymeqoverheadlineunbalanced[row.EquipmentId]['z32'] ='{:0.6f}{:+0.6}j'.format(float(row.MutualResistanceBC)*5280/(m2ft*1000), float(row.MutualReactanceBC)*5280/(m2ft*1000))  
                cymeqoverheadlineunbalanced[row.EquipmentId]['z13'] ='{:0.6f}{:+0.6}j'.format(float(row.MutualResistanceCA)*5280/(m2ft*1000), float(row.MutualReactanceCA)*5280/(m2ft*1000))  
                cymeqoverheadlineunbalanced[row.EquipmentId]['z31'] ='{:0.6f}{:+0.6}j'.format(float(row.MutualResistanceCA)*5280/(m2ft*1000), float(row.MutualReactanceCA)*5280/(m2ft*1000))  
    return cymeqoverheadlineunbalanced

def _findNumberOfPhase(equipmentID):
    phaseNumbers = []
    phases = re.findall('_[1-3]PH(?![a-zA-Z0-9])', equipmentID)
    for phase in phases:
        if phase[1] not in phaseNumbers:
            phaseNumbers.append(phase[1])
    if len(phaseNumbers) == 1:
        return phaseNumbers[0]
    else:
        warnings.warn("cannot decide number of phases from Equipment ID.")
        return '0'

def _readEqGeometricalArrangement(equipmentDatabase, feederId):
    '''store information from CYMEQGEOMETRICALARRANGEMENT. The read columns are 
    EquipmentId, ConductorA_Horizontal, ConductorA_Vertical, 
    ConductorB_Horizontal, ConductorB_Vertical, ConductorC_Horizontal, 
    ConductorC_Vertical, NeutralConductor_Horizontal, NeutralConductor_Vertical. 
    All values are assumed to be in meters.'''
    cymeqgeometricalarrangement = {}                           # Stores information found in CYMEQGEOMETRICALARRANGEMENT in the equipment database
    CYMEQGEOMETRICALARRANGEMENT = { 'name' : None,             # Information structure for each object found in CYMEQGEOMETRICALARRANGEMENT
                                    'distance_AB' : None,
                                    'distance_AC' : None,
                                    'distance_AN' : None,
                                    'distance_BC' : None,
                                    'distance_BN' : None,
                                    'distance_CN' : None}
    cymeqgeometricalarrangement_db = equipmentDatabase.execute("SELECT EquipmentId, ConductorA_Horizontal, ConductorA_Vertical, ConductorB_Horizontal, ConductorB_Vertical, ConductorC_Horizontal, ConductorC_Vertical, NeutralConductor_Horizontal, NeutralConductor_Vertical FROM CYMEQGEOMETRICALARRANGEMENT").fetchall()
    if len(cymeqgeometricalarrangement_db) == 0:
        warnings.warn("No geometric spacing information was found in CYMEQGEOMETRICALARRANGEMENT for feeder_id: {:s}.".format(feederId), RuntimeWarning)
    else:
        for row in cymeqgeometricalarrangement_db:
            row.EquipmentId = _fixName(row.EquipmentId)
            if row.EquipmentId not in cymeqgeometricalarrangement.keys():
                cymeqgeometricalarrangement[row.EquipmentId] = copy.deepcopy(CYMEQGEOMETRICALARRANGEMENT)
                cymeqgeometricalarrangement[row.EquipmentId]['name'] = row.EquipmentId
                # # parse row.EquipmentId to see if it's 3 phases or 2 phases or 1 phase
                # phase = _findNumberOfPhase(row.EquipmentId)
                # if phase and phase == '1':
                #     cymeqgeometricalarrangement[row.EquipmentId]['distance_AN'] = math.sqrt((float(row.ConductorA_Horizontal)-float(row.NeutralConductor_Horizontal))**2 + (float(row.ConductorA_Vertical)-float(row.NeutralConductor_Vertical))**2)*m2ft # information is stored in meters. must convert to feet.
                #     cymeqgeometricalarrangement[row.EquipmentId]['distance_BN'] = cymeqgeometricalarrangement[row.EquipmentId]['distance_AN'] # information is stored in meters. must convert to feet.
                #     cymeqgeometricalarrangement[row.EquipmentId]['distance_CN'] = cymeqgeometricalarrangement[row.EquipmentId]['distance_AN'] # information is stored in meters. must convert to feet.
                #     cymeqgeometricalarrangement[row.EquipmentId]['distance_AB'] = 0 # information is stored in meters. must convert to feet.
                #     cymeqgeometricalarrangement[row.EquipmentId]['distance_AC'] = 0 # information is stored in meters. must convert to feet.
                #     cymeqgeometricalarrangement[row.EquipmentId]['distance_BC'] = 0 # information is stored in meters. must convert to feet.
                # elif phase and phase == '2':
                #     cymeqgeometricalarrangement[row.EquipmentId]['distance_AB'] = math.sqrt((float(row.ConductorA_Horizontal)-float(row.ConductorB_Horizontal))**2 + (float(row.ConductorA_Vertical)-float(row.ConductorB_Vertical))**2)*m2ft # information is stored in meters. must convert to feet.
                #     cymeqgeometricalarrangement[row.EquipmentId]['distance_AN'] = math.sqrt((float(row.ConductorA_Horizontal)-float(row.NeutralConductor_Horizontal))**2 + (float(row.ConductorA_Vertical)-float(row.NeutralConductor_Vertical))**2)*m2ft # information is stored in meters. must convert to feet.
                #     cymeqgeometricalarrangement[row.EquipmentId]['distance_BN'] = math.sqrt((float(row.NeutralConductor_Horizontal)-float(row.ConductorB_Horizontal))**2 + (float(row.NeutralConductor_Vertical)-float(row.ConductorB_Vertical))**2)*m2ft # information is stored in meters. must convert to feet.
                #     cymeqgeometricalarrangement[row.EquipmentId]['distance_CN'] = cymeqgeometricalarrangement[row.EquipmentId]['distance_AN'] # information is stored in meters. must convert to feet.
                #     cymeqgeometricalarrangement[row.EquipmentId]['distance_AC'] = cymeqgeometricalarrangement[row.EquipmentId]['distance_AB'] # information is stored in meters. must convert to feet.
                #     cymeqgeometricalarrangement[row.EquipmentId]['distance_BC'] = 0 # information is stored in meters. must convert to feet.
                # else: # if 3 phases, or cannot decide number of phases or default
                cymeqgeometricalarrangement[row.EquipmentId]['distance_AB'] = math.sqrt((float(row.ConductorA_Horizontal)-float(row.ConductorB_Horizontal))**2 + (float(row.ConductorA_Vertical)-float(row.ConductorB_Vertical))**2)*m2ft # information is stored in meters. must convert to feet.
                cymeqgeometricalarrangement[row.EquipmentId]['distance_AC'] = math.sqrt((float(row.ConductorA_Horizontal)-float(row.ConductorC_Horizontal))**2 + (float(row.ConductorA_Vertical)-float(row.ConductorC_Vertical))**2)*m2ft # information is stored in meters. must convert to feet.
                cymeqgeometricalarrangement[row.EquipmentId]['distance_AN'] = math.sqrt((float(row.ConductorA_Horizontal)-float(row.NeutralConductor_Horizontal))**2 + (float(row.ConductorA_Vertical)-float(row.NeutralConductor_Vertical))**2)*m2ft # information is stored in meters. must convert to feet.
                cymeqgeometricalarrangement[row.EquipmentId]['distance_BC'] = math.sqrt((float(row.ConductorC_Horizontal)-float(row.ConductorB_Horizontal))**2 + (float(row.ConductorC_Vertical)-float(row.ConductorB_Vertical))**2)*m2ft # information is stored in meters. must convert to feet.
                cymeqgeometricalarrangement[row.EquipmentId]['distance_BN'] = math.sqrt((float(row.NeutralConductor_Horizontal)-float(row.ConductorB_Horizontal))**2 + (float(row.NeutralConductor_Vertical)-float(row.ConductorB_Vertical))**2)*m2ft # information is stored in meters. must convert to feet.
                cymeqgeometricalarrangement[row.EquipmentId]['distance_CN'] = math.sqrt((float(row.ConductorC_Horizontal)-float(row.NeutralConductor_Horizontal))**2 + (float(row.ConductorC_Vertical)-float(row.NeutralConductor_Vertical))**2)*m2ft # information is stored in meters. must convert to feet.
                cymeqgeometricalarrangement[row.EquipmentId]['distance_AE'] = float(row.ConductorA_Vertical)*m2ft # information is stored in meters. must convert to feet.
                cymeqgeometricalarrangement[row.EquipmentId]['distance_BE'] = float(row.ConductorB_Vertical)*m2ft # information is stored in meters. must convert to feet.
                cymeqgeometricalarrangement[row.EquipmentId]['distance_CE'] = float(row.ConductorC_Vertical)*m2ft # information is stored in meters. must convert to feet.
                cymeqgeometricalarrangement[row.EquipmentId]['distance_NE'] = float(row.NeutralConductor_Vertical)*m2ft # information is stored in meters. must convert to feet.
    return cymeqgeometricalarrangement

def _readUgConfiguration(conductorDataCsv, feederId):
    cymcsvundergroundcable = {}
    CYMCSVUNDERGROUNDCABLE = { 'name' : None,
                               'rating.summer_continuous' : None,
                               'outer_diameter' : None,
                               'conductor_resistance' : None,
                               'conductor_gmr' : None,
                               'conductor_diameter' : None,
                               'neutral_resistance' : None,
                               'neutral_gmr' : None,
                               'neutral_diameter' : None,
                               'neutral_strands' : None,
                               'distance_AB' : None,
                               'distance_AC' : None,
                               'distance_AN' : None,
                               'distance_BC' : None,
                               'distance_BN' : None,
                               'distance_CN' : None}
    
    if conductorDataCsv != None:
        underground_cable_array = _csvToArray(conductorDataCsv)[1:]# skip the first row as it is header information
        for row in underground_cable_array:
            if _fixName(row[0]) not in list(cymcsvundergroundcable.keys()):
                cymcsvundergroundcable[_fixName(row[0])] = copy.deepcopy(CYMCSVUNDERGROUNDCABLE)
                cymcsvundergroundcable[_fixName(row[0])]['name'] = _fixName(row[0])
                cymcsvundergroundcable[_fixName(row[0])]['conductor_resistance'] = row[2]
                cymcsvundergroundcable[_fixName(row[0])]['conductor_gmr'] = row[11]
                cymcsvundergroundcable[_fixName(row[0])]['rating.summer_continuous'] = row[16]
                cymcsvundergroundcable[_fixName(row[0])]['conductor_diameter'] = row[17]
                cymcsvundergroundcable[_fixName(row[0])]['neutral_resistance'] = row[19]
                cymcsvundergroundcable[_fixName(row[0])]['neutral_gmr'] = row[21]
                cymcsvundergroundcable[_fixName(row[0])]['neutral_diameter'] = row[18]
                cymcsvundergroundcable[_fixName(row[0])]['neutral_strands'] = row[20]
                cymcsvundergroundcable[_fixName(row[0])]['outer_diameter'] = row[19]
                cymcsvundergroundcable[_fixName(row[0])]['distance_AB'] = row[24]
                cymcsvundergroundcable[_fixName(row[0])]['distance_AC'] = row[26]
                cymcsvundergroundcable[_fixName(row[0])]['distance_AN'] = row[27]
                cymcsvundergroundcable[_fixName(row[0])]['distance_BC'] = row[25]
                cymcsvundergroundcable[_fixName(row[0])]['distance_BN'] = row[28]
                cymcsvundergroundcable[_fixName(row[0])]['distance_CN'] = row[29]
    else:
        warnings.warn("No conductor data spreadsheet is provided for feeder_id {:s}.".format(feederId), RuntimeWarning)
    return cymcsvundergroundcable

def _readEqAvgGeometricalArrangement(equipmentDatabase, feederId):
    '''store information from CYMEQAVERAGEGEOARRANGEMENT. The read columns are 
    SELECT EquipmentId, GMDPhaseToPhase, and GMDPhaseToNeutral. All values are 
    assumed to be in meters.'''
    cymeqgeometricalarrangement = {}
    CYMEQGEOMETRICALARRANGEMENT = {'name' : None,
                                 'distance_AB' : None,
                                 'distance_AC' : None,
                                 'distance_AN' : None,
                                 'distance_BC' : None,
                                 'distance_BN' : None,
                                 'distance_CN' : None}
    cymeqaveragegeoarrangement_db = equipmentDatabase.execute("SELECT EquipmentId, GMDPhaseToPhase, GMDPhaseToNeutral FROM CYMEQAVERAGEGEOARRANGEMENT").fetchall()
    if len(cymeqaveragegeoarrangement_db) == 0:
        warnings.warn("No average spacing information was found in CYMEQAVERAGEGEOARRANGEMENT for feeder_id: {:s}.".format(feederId), RuntimeWarning)
    else:
        for row in cymeqaveragegeoarrangement_db:
            row.EquipmentId = _fixName(row.EquipmentId)
            if row.EquipmentId not in list(cymeqgeometricalarrangement.keys()):
                cymeqgeometricalarrangement[row.EquipmentId] = copy.deepcopy(CYMEQGEOMETRICALARRANGEMENT)
                cymeqgeometricalarrangement[row.EquipmentId]['name'] = row.EquipmentId             
                cymeqgeometricalarrangement[row.EquipmentId]['distance_AB'] = float(row.GMDPhaseToPhase)*m2ft # information is stored in meters. must convert to feet.
                cymeqgeometricalarrangement[row.EquipmentId]['distance_AC'] = float(row.GMDPhaseToPhase)*m2ft
                cymeqgeometricalarrangement[row.EquipmentId]['distance_AN'] = float(row.GMDPhaseToNeutral)*m2ft
                cymeqgeometricalarrangement[row.EquipmentId]['distance_BC'] = float(row.GMDPhaseToPhase)*m2ft
                cymeqgeometricalarrangement[row.EquipmentId]['distance_BN'] = float(row.GMDPhaseToNeutral)*m2ft
                cymeqgeometricalarrangement[row.EquipmentId]['distance_CN'] = float(row.GMDPhaseToNeutral)*m2ft
    return cymeqgeometricalarrangement

def _readEqRegulator(equipmentDatabase, feederId):
    '''store information from CYMEQREGULATOR. The read columns are 
    SELECT EquipmentId and NumberOfTaps.'''
    cymeqregulator = {}                           # Stores information found in CYMEQREGULATOR in the equipment database
    CYMEQREGULATOR = { 'name' : None,             # Information structure for each object found in CYMEQREGULATOR
                       'raise_taps' : None,
                       'lower_taps' : None}
    cymeqregulator_db = equipmentDatabase.execute("SELECT EquipmentId, NumberOfTaps FROM CYMEQREGULATOR").fetchall()
    if len(cymeqregulator_db) == 0:
        warnings.warn("No regulator equipment was found in CYMEQREGULATOR for feeder_id: {:s}.".format(feederId), RuntimeWarning)
    else:
        for row in cymeqregulator_db:
            row.EquipmentId = _fixName(row.EquipmentId)
            if row.EquipmentId not in list(cymeqregulator.keys()):
                cymeqregulator[row.EquipmentId] = copy.deepcopy(CYMEQREGULATOR)
                cymeqregulator[row.EquipmentId]['name'] = row.EquipmentId           
                cymeqregulator[row.EquipmentId]['raise_taps'] = str(int(float(row.NumberOfTaps) * 0.5))
                cymeqregulator[row.EquipmentId]['lower_taps'] = str(int(float(row.NumberOfTaps) * 0.5))
    return cymeqregulator

def _readEqThreeWAutoXfmr(equipmentDatabase, feederId):
    '''store information from CYMEQTHREEWINDAUTOTRANSFORMER. The read columns are 
    SELECT EquipmentId, PrimaryRatedCapacity, PrimaryVoltage, SecondaryVoltage, 
    PrimarySecondaryZ1, PrimarySecondaryZ0, PrimarySecondaryXR1Ratio, 
    and PrimarySecondaryXR0Ratio. voltages are assumed to be the line to line
    voltage in kV.'''
    cymeqthreewautoxfmr = {}                           # Stores information found in CYMEQTHREEWINDAUTOTRANSFORMER in the equipment database
    CYMEQTHREEWAUTOXFMR = { 'name' : None,             # Information structure for each object found in CYMEQTHREEWINDAUTOTRANSFORMER
                          'PrimaryRatedCapacity' : None,
                          'PrimaryVoltage' : None,
                          'SecondaryVoltage' : None,
                          'impedance' : None}

    cymeqthreewautoxfmr_db = equipmentDatabase.execute("SELECT EquipmentId, PrimaryRatedCapacity, PrimaryVoltage, SecondaryVoltage, PrimarySecondaryZ1, PrimarySecondaryZ0, PrimarySecondaryXR1Ratio, PrimarySecondaryXR0Ratio  FROM CYMEQTHREEWINDAUTOTRANSFORMER").fetchall()
    if len(cymeqthreewautoxfmr_db) == 0:
        warnings.warn("No average spacing information was found in CYMEQTHREEWINDAUTOTRANSFORMER for feeder_id: {:s}.".format(feederId), RuntimeWarning)
    else:
        for row in cymeqthreewautoxfmr_db:
            row.EquipmentId = _fixName(row.EquipmentId)
            if row.EquipmentId not in list(cymeqthreewautoxfmr.keys()):
                cymeqthreewautoxfmr[row.EquipmentId] = copy.deepcopy(CYMEQTHREEWAUTOXFMR)
                cymeqthreewautoxfmr[row.EquipmentId]['name'] = row.EquipmentId           
                cymeqthreewautoxfmr[row.EquipmentId]['PrimaryRatedCapacity'] = float(row.PrimaryRatedCapacity)
                cymeqthreewautoxfmr[row.EquipmentId]['PrimaryVoltage'] = float(row.PrimaryVoltage)*1000.0/math.sqrt(3.0)
                cymeqthreewautoxfmr[row.EquipmentId]['SecondaryVoltage'] = float(row.SecondaryVoltage)*1000.0/math.sqrt(3.0)
                if cymeqthreewautoxfmr[row.EquipmentId]['PrimaryVoltage'] == cymeqthreewautoxfmr[row.EquipmentId]['SecondaryVoltage']:
                    cymeqthreewautoxfmr[row.EquipmentId]['SecondaryVoltage'] += 0.1
                z1mag = float(row.PrimarySecondaryZ1)/100.0
                r = z1mag/math.sqrt(1+(float(row.PrimarySecondaryXR1Ratio))**2)
                if r == 0.0:
                    r = 0.000333
                    x = 0.00222
                else:
                    x = r*float(row.PrimarySecondaryXR1Ratio)
                cymeqthreewautoxfmr[row.EquipmentId]['impedance'] = '{:0.6f}{:+0.6f}j'.format(r, x)
    return cymeqthreewautoxfmr

def _readEqAutoXfmr(equipmentDatabase, feederId):
    '''store information from CYMEQTHREEWINDAUTOTRANSFORMER. The read columns are 
    SELECT EquipmentId, NominalRatingKVA, PrimaryVoltageKVLL, 
    SecondaryVoltageKVLL, PosSeqImpedancePercent, XRRatio. voltages are assumed 
    to be the line to line voltage in kV.'''
    cymeqautoxfmr = {}
    CYMEQAUTOXFMR = { 'name' : None,
                     'PrimaryRatedCapacity' : None,
                     'PrimaryVoltage' : None,
                     'SecondaryVoltage' : None,
                     'impedance' : None}
    cymeqautoxfmr_db = equipmentDatabase.execute("SELECT EquipmentId, NominalRatingKVA, PrimaryVoltageKVLL, SecondaryVoltageKVLL, PosSeqImpedancePercent, XRRatio FROM CYMEQAUTOTRANSFORMER").fetchall()
    if len(cymeqautoxfmr_db) == 0:
        warnings.warn("No average autotransformer equipment information was found in CYMEQAUTOTRANSFORMER for feeder id: {:s}.".format(feederId), RuntimeWarning)
    else:
        for row in cymeqautoxfmr_db:
            row.EquipmentId = _fixName(row.EquipmentId)
            if row.EquipmentId not in list(cymeqautoxfmr.keys()):
                cymeqautoxfmr[row.EquipmentId] = copy.deepcopy(CYMEQAUTOXFMR)
                cymeqautoxfmr[row.EquipmentId]['name'] = row.EquipmentId           
                cymeqautoxfmr[row.EquipmentId]['PrimaryRatedCapacity'] = float(row.NominalRatingKVA)
                cymeqautoxfmr[row.EquipmentId]['PrimaryVoltage'] = float(row.PrimaryVoltageKVLL)*1000.0/math.sqrt(3.0)
                cymeqautoxfmr[row.EquipmentId]['SecondaryVoltage'] = float(row.SecondaryVoltageKVLL)*1000.0/math.sqrt(3.0)
                if cymeqautoxfmr[row.EquipmentId]['PrimaryVoltage'] == cymeqautoxfmr[row.EquipmentId]['SecondaryVoltage']:
                    cymeqautoxfmr[row.EquipmentId]['SecondaryVoltage'] += 0.001
                z1mag = float(row.PosSeqImpedancePercent)/100.0
                r = z1mag/math.sqrt(1+(float(row.XRRatio))**2)
                if r == 0.0:
                    r = 0.000333
                    x = 0.00222
                else:
                    x = r*float(row.XRRatio)
                cymeqautoxfmr[row.EquipmentId]['impedance'] = '{:0.6f}{:+0.6f}j'.format(r, x)
    return cymeqautoxfmr


def _readCymOverheadLine(networkDatabase, feederId):
    '''store information from CYMOVERHEADLINE. The
    expected columns that are read are DeviceNumber, LineId, and Length. Length is assumed to be in meters.'''
    cymoverheadLine = {}     # Stores information found in CYMOVERHEADBYPHASE in the network database
    # if olc is None:
    #     olc = {}
    # thisolc = {}
    # olcDevice = {}              # store device information for each configuration
    # uniqueSpacing = []
    # overheadConductors = []     # Stores the unique conductor equipment Ids
    CYMOVERHEADLINE = { 'name' : None,       # Information structure for each object found in CYMOVERHEADLINE
                          'length' : None,
                          'configuration' : None}
    overheadline_db = networkDatabase.execute("SELECT DeviceNumber, LineId, Length FROM CYMOVERHEADLINE WHERE NetworkId = '{:s}'".format(feederId)).fetchall()
    if len(overheadline_db) == 0:
        warnings.warn("No information on LineId and lengths were found in CYMOVERHEADLINE for feeder_id: {:s}.".format(feederId), RuntimeWarning)
    else:
        # Add all phase conductors to the line configuration dictionary.
        for row in overheadline_db:
            overheadLineConfiguration = {}
            row.DeviceNumber = _fixName(row.DeviceNumber)
            if row.DeviceNumber not in cymoverheadLine.keys():
                cymoverheadLine[row.DeviceNumber] = copy.deepcopy(CYMOVERHEADLINE)
                cymoverheadLine[row.DeviceNumber]['name'] = row.DeviceNumber
                cymoverheadLine[row.DeviceNumber]['length'] = row.Length
                cymoverheadLine[row.DeviceNumber]['configuration'] = row.LineId
                # # overheadLineConfiguration['spacing'] = _fixName(row.ConductorSpacingId)
                # if _fixName(row.ConductorSpacingId) not in uniqueSpacing:
                #     uniqueSpacing.append(_fixName(row.ConductorSpacingId))
                # cymoverheadbyphase[row.DeviceNumber]['length'] = float(row.Length)*m2ft
                # if cymoverheadbyphase[row.DeviceNumber]['length'] == 0.0:
                #     cymoverheadbyphase[row.DeviceNumber]['length'] = 1.0
                # # if len(olc) == 0:
                # #     olc['olc0'] = copy.deepcopy(overheadLineConfiguration)
                # #     cymoverheadbyphase[row.DeviceNumber]['configuration'] = 'olc0'
                # #
                # # else:
                # for key in olc.keys():
                #     if overheadLineConfiguration == olc[key]:
                #         cymoverheadbyphase[row.DeviceNumber]['configuration'] = key
                #         if key not in olcDevice.keys():
                #             olcDevice[key] = list()
                #         olcDevice[key].append(row.DeviceNumber)
                #         if key not in thisolc:
                #             thisolc[key] = copy.deepcopy(overheadLineConfiguration)
                #         break
                # if cymoverheadbyphase[row.DeviceNumber]['configuration'] == None:
                #     key = 'olc' + str(len(olc))
                #     olc[key] = copy.deepcopy(overheadLineConfiguration)
                #     cymoverheadbyphase[row.DeviceNumber]['configuration'] = key
                #     if key not in olcDevice.keys():
                #         olcDevice[key] = list()
                #     olcDevice[key].append(row.DeviceNumber)
                #     if key not in thisolc:
                #         thisolc[key] = copy.deepcopy(overheadLineConfiguration)
    return cymoverheadLine


def _find_SPCT_rating(load_str):
        try:
            spot_load = abs(complex(load_str))/1000.0
        except:
            print(("load_str = {}".format(load_str)))
            raise			
        spct_rating = [5,10,15,25,30,37.5,50,75,87.5,100,112.5,125,137.5,150,162.5,175,187.5,200,225,250,262.5,300,337.5,400,412.5,450,500,750,1000,1250,1500,2000,2500,3000,4000,5000]
        past_rating = max(spct_rating)
        for rating in spct_rating:
            if rating >= spot_load and rating < past_rating:
                past_rating = rating
        return str(past_rating)


def _findUniqueEquipment(dict1, dict2):
    for k, v in dict1.items():
        if k in dict2:
            if v == dict2[k]:
                pass
            else:
                warnings.warn(str(k) + " found in both feeders, but have different value.", RuntimeWarning)
        else:
            dict2[k] = v


def convertCymeModel(network_db, equipment_db, type=1, feeder_id=None, primaryLoadOnly=True, ugConfig='', equipments=None, overheadlineConfig=None, deltaConnections=None, connectedNodes=None):
    network_db = Path(network_db).resolve()
    equipment_db = Path(equipment_db).resolve()
    conductor_data_csv = None
    if equipments is None:
        equipments = {}
    dbflag = 0 
    if 'Duke' in str(network_db):
        dbflag = 0
    elif 'Paso' in str(network_db):
        dbflag= 1
    header_glmTree = {}    # Dictionary that will hold the feeder model for conversion to .glm format
    network_glmTree = {}    # Dictionary that will hold the feeder model for conversion to .glm format
    regulator_sections = {}
    recloser_sections = {}
    sectionalizer_sections = {}
    switch_sections = {}
    fuse_sections = {}
    capacitor_sections = {}
    vsc_sections = {}
    threewautoxfmr_sections = {}
    transformer_sections = {}
    overheadline_sections = {}
    undergroundline_sections = {}
    sx_section = []
    pv_sections = {}
    load_sections = {}
    threewxfmr_sections = {}
    # Open the network database file
    net_db = _openDatabase(network_db)
    # -1-CYME CYMSOURCE *********************************************************************************************************************************************************************
    cymsource, feeder_id, swingBus = _readCymeSource(net_db, feeder_id, type)
    # -2-CYME CYMNODE *********************************************************************************************************************************************************************
    cymnode, x_scale, y_scale = _readCymeNode(net_db, feeder_id)
    # -3-CYME OVERHEADBYPHASE ****************************************************************************************************************************************************************
    OH_conductors, cymoverheadbyphase, ohConfigurations, uniqueOhSpacing, ohDeviceByConfig = _readCymeOverheadByPhase(net_db, feeder_id, olc=overheadlineConfig)
    # -4-CYME UNDERGROUNDLINE ****************************************************************************************************************************************************************
    UG_conductors, cymundergroundline = _readCymeUndergroundLine(net_db, feeder_id)
    # -5-CYME CYMOVERHEADLINEBALANCED ****************************************************************************************************************************************************************
    cymUnbalancedOverheadLine, UOLConfigNames = _readCymeOverheadLineUnbalanced(net_db, feeder_id)
    # -5-CYME CYMSWITCH**********************************************************************************************************************************************************************
    cymswitch = _readCymeSwitch(net_db, feeder_id)
    # -6-CYME CYMSECTIONALIZER**********************************************************************************************************************************************************************
    cymsectionalizer = _readCymeSectionalizer(net_db, feeder_id)        
    # -7-CYME CYMFUSE**********************************************************************************************************************************************************************
    cymfuse = _readCymeFuse(net_db, feeder_id)
    # -8-CYME CYMRECLOSER**********************************************************************************************************************************************************************
    cymrecloser = _readCymeRecloser(net_db, feeder_id)
    # -9-CYME CYMREGULATOR**********************************************************************************************************************************************************************
    cymregulator = _readCymeRegulator(net_db, feeder_id)
    # -10-CYME CYMSHUNTCAPACITOR**********************************************************************************************************************************************************************
    cymshuntcapacitor = _readCymeShuntCapacitor(net_db, feeder_id, type)
    # -11-CYME CYMCUSTOMERLOAD**********************************************************************************************************************************************************************
    cymcustomerload = _readCymeCustomerLoad(net_db, feeder_id)
    # -12-CYME CYMSECTION ****************************************************************************************************************************************************************
    cymsection = _readCymeSection(net_db, feeder_id)
    for section in cymsection.keys():
        fromNode = cymsection[section]['from']
        toNode = cymsection[section]['to']
        if fromNode in list(cymnode.keys()):
            cymsection[section]['fromX'] = cymnode[fromNode]['latitude']
            cymsection[section]['fromY'] = cymnode[fromNode]['longitude']
        else:
            cymsection[section]['fromX'] = '0'
            cymsection[section]['fromY'] = '800'
        if toNode in list(cymnode.keys()):
            cymsection[section]['toX'] = cymnode[toNode]['latitude']
            cymsection[section]['toY'] = cymnode[toNode]['longitude']
        else:
            cymsection[section]['toX'] = '0'
            cymsection[section]['toY'] = '800'
    # -13-CYME CYMSECTIONDEVICE ****************************************************************************************************************************************************************
    cymsectiondevice = _readCymeSectionDevice(net_db, feeder_id) 
    # Check that the section actually is a device
    for link in cymsection.keys():
        link_exists = 0
        for device in cymsectiondevice.keys():
            if cymsectiondevice[device]['section_name'] == link:
                link_exists = 1
        if link_exists == 0:
            cymsection[link]['connector'] = ''
            warnings.warn("There is no device associated with section:{:s} in network database:{:s}. This will be modeled as a switch.".format(link, net_db), RuntimeWarning)
    for link in cymsection.keys():
        if 'connector' in cymsection[link].keys():
            cymsectiondevice[link] = { 'name' : link,
                                        'device_type' : 13,
                                        'section_name' : link,
                                        'location' : 0}
            cymswitch[link] = { 'name' : link,            # Information structure for each object found in CYMSWITCH
                              'equipment_name' : None,
                              'status' : 1}
            del cymsection[link]['connector']
    # Remove islands from the network database
    fromNodes = []
    toNodes = []
    for link in cymsection.keys():
        if 'from' in cymsection[link].keys():
            if cymsection[link]['from'] not in fromNodes:
                fromNodes.append(cymsection[link]['from'])
            if cymsection[link]['to'] not in toNodes:
                toNodes.append(cymsection[link]['to'])
    islandNodes = []
    for node in fromNodes:
        if node not in toNodes and node != swingBus and node not in islandNodes:
            islandNodes.append(node)
    islands = 0
    nislands = len(islandNodes)
    while nislands != islands:
        print('nislands: ' + str(nislands))
        islands = len(islandNodes)
        toislands = []
        for link in cymsection.keys():
            if 'from' in cymsection[link].keys():
                if cymsection[link]['from'] in islandNodes and cymsection[link]['to'] not in islandNodes:
                    # print('island, from: ' + cymsection[link]['from'] + ', to: ' + cymsection[link]['to'])
                    toislands.append(cymsection[link]['to'])
        islandNodes += toislands
        nislands = len(islandNodes)
    deleteSections = []
    for node in islandNodes:
        for link in cymsection.keys():
            if (node == cymsection[link]['from'] or node == cymsection[link]['to']) and link not in deleteSections:
                deleteSections.append(link)
    for section in deleteSections:
        pass
        # del cymsection[section]
    for device in cymsectiondevice.keys():
        if cymsectiondevice[device]['section_name'] in deleteSections:
            pass
            # del cymsectiondevice[device]
    # Group each type of device.
    for device in cymsectiondevice.keys():
        if cymsectiondevice[device]['device_type'] == 1:
            undergroundline_sections[cymsectiondevice[device]['section_name']] = device 
        elif cymsectiondevice[device]['device_type'] == 3:
            overheadline_sections[cymsectiondevice[device]['section_name']] = device
        elif cymsectiondevice[device]['device_type'] == 4:
            regulator_sections[cymsectiondevice[device]['section_name']] = device
        elif cymsectiondevice[device]['device_type'] == 10: 
            recloser_sections[cymsectiondevice[device]['section_name']] = device
        elif cymsectiondevice[device]['device_type'] == 12:
            sectionalizer_sections[cymsectiondevice[device]['section_name']] = device
        elif cymsectiondevice[device]['device_type'] == 13:
            switch_sections[cymsectiondevice[device]['section_name']] = device
        elif cymsectiondevice[device]['device_type'] == 14:
            fuse_sections[cymsectiondevice[device]['section_name']] = device
        elif cymsectiondevice[device]['device_type'] == 16:
            sx_section.append(cymsectiondevice[device]['section_name'])
        elif cymsectiondevice[device]['device_type'] == 17:
            capacitor_sections[cymsectiondevice[device]['section_name']] = device
        elif cymsectiondevice[device]['device_type'] == 20:
            load_sections[cymsectiondevice[device]['section_name']] = device
        elif cymsectiondevice[device]['device_type'] == 45:
            if dbflag == 0: # duke
                vsc_sections[cymsectiondevice[device]['section_name']] = device
            elif dbflag == 1: # paso
                pv_sections[cymsectiondevice[device]['section_name']] = device
        elif cymsectiondevice[device]['device_type'] == 47:
            transformer_sections[cymsectiondevice[device]['section_name']] = device
        elif cymsectiondevice[device]['device_type'] == 48:
            if dbflag == 0: # duke
                threewautoxfmr_sections[cymsectiondevice[device]['section_name']] = device
            elif dbflag == 1: # paso
                threewxfmr_sections[cymsectiondevice[device]['section_name']] = device
    # find the parent of capacitors, loads, and pv
    for x in [capacitor_sections, load_sections, pv_sections]:
        if len(x) > 0:
            _findParents(cymsection, cymsectiondevice, x)
    # split out fuses, regulators, transformers, switches, reclosers, and sectionalizers from the lines.
    # mj debug: check these phases
    for x in [fuse_sections, regulator_sections, threewxfmr_sections, threewautoxfmr_sections, transformer_sections, switch_sections, recloser_sections, sectionalizer_sections]:
        if len(x) > 0:
            _splitLinkObjects(cymsection, cymsectiondevice, x, overheadline_sections, undergroundline_sections)          
    # -14-CYME CYMTRANSFORMER**********************************************************************************************************************************************************************
    cymxfmr = _readCymeTransformer(net_db, feeder_id)
    # -15-CYME CYMTHREEWINDINGTRANSFORMER******************************************************************************************************************************************************************************************
    cym3wxfmr = _readCymeThreeWindingTransformer(net_db, feeder_id)
    net_db.close()
    # Open the equipment database file
    eqp_db = _openDatabase(equipment_db)
    # -16-CYME CYMEQCONDUCTOR**********************************************************************************************************************************************************************
    cymeqconductor = _readEqConductor(eqp_db, feeder_id)
    # -17-CYME CYMEQCONDUCTOR**********************************************************************************************************************************************************************
    cymeqoverheadlineunbalanced = _readEqOverheadLineUnbalanced(eqp_db, feeder_id)
    # -17-CYME CYMEQGEOMETRICALARRANGEMENT**********************************************************************************************************************************************************************
    if dbflag == 0: # duke
        cymeqgeometricalarrangement = _readEqGeometricalArrangement(eqp_db, feeder_id) 
    elif dbflag == 1: #paso
        cymeqgeometricalarrangement = _readEqAvgGeometricalArrangement(eqp_db, feeder_id)
    # -18-CYME Read XLSX Sheet**********************************************************************************************************************************************************************
    cymcsvundergroundcable = _readUgConfiguration(conductor_data_csv, feeder_id)
    # -19-CYME CYMEQREGULATOR**********************************************************************************************************************************************************************
    cymeqregulator = _readEqRegulator(eqp_db, feeder_id)
    # -20-CYME CYMEQTHREEWINDAUTOTRANSFORMER**********************************************************************************************************************************************************************
    cymeq3wautoxfmr = _readEqThreeWAutoXfmr(eqp_db, feeder_id)
    # -21-CYME CYMEQAUTOTRANSFORMER**********************************************************************************************************************************************************************
    cymeqautoxfmr = _readEqAutoXfmr(eqp_db, feeder_id)
    # for ComED, read the CYMOVERHEADLINE
    ohl_type2 = _readCymOverheadLine(eqp_db, feeder_id)
    # FINISHED READING FROM THE DATABASES*****************************************************************************************************************************************************
    eqp_db.close()
    
    # Check number of sources
    meters = {}
    if len(cymsource) > 1:
        print("There is more than one swing bus for feeder_id ", feeder_id, "\n")      
    for x in cymsource.keys():
        meters[x] = { 'object' : 'meter',
                         'name' : '{:s}'.format(cymsource[x]['name']),
                         'bustype' : 'SWING',
                         'nominal_voltage' : cymsource[x]['nominal_voltage'],
                         'latitude' : cymnode[x]['latitude'],
                         'longitude' : cymnode[x]['longitude']}
        feeder_VLN = cymsource[x]['nominal_voltage']
    # Check for parallel links and islands
    fromTo = []
    fromNodes = []
    toNodes = []
    parallelLinks = []
    for link in cymsection.keys():
        if 'from' in cymsection[link].keys() and 'to' in cymsection[link].keys():
            if [cymsection[link]['from'], cymsection[link]['to']] in fromTo or [cymsection[link]['to'], cymsection[link]['from']] in fromTo:
                for key in cymsectiondevice.keys():
                    if cymsectiondevice[key]['section_name'] == link:
                        parallelLinks.append(key)
            else:
                fromTo.append([cymsection[link]['from'], cymsection[link]['to']])
            if cymsection[link]['from'] not in fromNodes:
                fromNodes.append(cymsection[link]['from'])
            if cymsection[link]['to'] not in toNodes:
                toNodes.append(cymsection[link]['to'])
    islandNodes = []
    for node in fromNodes:
        if node not in toNodes and node != swingBus and node not in islandNodes:
            islandNodes.append(node)
    for node in islandNodes:
        if node != swingBus:
            print("Feeder islanded\n")            
    # Pass from, to, and phase information from cymsection to cymsectiondevice
    nodes = {}
    ohPhasesByConfig = {}
    for device in cymsectiondevice.keys():
        if 'parent' not in cymsectiondevice[device].keys():
            cymsectiondevice[device]['from'] = cymsection[cymsectiondevice[device]['section_name']]['from']
            cymsectiondevice[device]['to'] = cymsection[cymsectiondevice[device]['section_name']]['to']
            cymsectiondevice[device]['phases'] = cymsection[cymsectiondevice[device]['section_name']]['phases']
            cymsectiondevice[device]['fromLatitude'] = cymsection[cymsectiondevice[device]['section_name']]['fromX']
            cymsectiondevice[device]['fromLongitude'] = cymsection[cymsectiondevice[device]['section_name']]['fromY']
            cymsectiondevice[device]['toLatitude'] = cymsection[cymsectiondevice[device]['section_name']]['toX']
            cymsectiondevice[device]['toLongitude'] = cymsection[cymsectiondevice[device]['section_name']]['toY']
        # Create all the node dictionaries
            if cymsectiondevice[device]['from'] not in nodes.keys() and cymsectiondevice[device]['from'] != swingBus:
                nodes[cymsectiondevice[device]['from']] = {'object' : 'node',
                                                            'name' : cymsectiondevice[device]['from'],
                                                            'phases' : cymsectiondevice[device]['phases'],
                                                            'nominal_voltage' : str(feeder_VLN),
                                                            'latitude' : cymsectiondevice[device]['fromLatitude'],
                                                            'longitude' : cymsectiondevice[device]['fromLongitude']}
            if cymsectiondevice[device]['to'] not in nodes.keys() and cymsectiondevice[device]['to'] != swingBus:
                nodes[cymsectiondevice[device]['to']] = {'object' : 'node',
                                                        'name' : cymsectiondevice[device]['to'],
                                                        'phases' : cymsectiondevice[device]['phases'],
                                                        'nominal_voltage' : str(feeder_VLN),
                                                        'latitude' : cymsectiondevice[device]['toLatitude'],
                                                        'longitude' : cymsectiondevice[device]['toLongitude']}
        else:
            cymsectiondevice[device]['fromLatitude'] = cymsection[cymsectiondevice[device]['section_name']]['fromX']
            cymsectiondevice[device]['fromLongitude'] = cymsection[cymsectiondevice[device]['section_name']]['fromY']
            cymsectiondevice[device]['toLatitude'] = cymsection[cymsectiondevice[device]['section_name']]['toX']
            cymsectiondevice[device]['toLongitude'] = cymsection[cymsectiondevice[device]['section_name']]['toY']
            if cymsectiondevice[device]['parent'] not in nodes.keys() and cymsectiondevice[device]['parent'] != swingBus:
                nodes[cymsectiondevice[device]['parent']] = {'object' : 'node',
                                                            'name' : cymsectiondevice[device]['parent'],
                                                            'phases' : cymsectiondevice[device]['phases'],
                                                            'nominal_voltage' : str(feeder_VLN)}
                if cymsectiondevice[device]['location'] == 2:
                    nodes[cymsectiondevice[device]['parent']]['latitude'] = cymsectiondevice[device]['toLatitude']
                    nodes[cymsectiondevice[device]['parent']]['longitude'] = cymsectiondevice[device]['toLongitude']
                else:
                    nodes[cymsectiondevice[device]['parent']]['latitude'] = cymsectiondevice[device]['fromLatitude']
                    nodes[cymsectiondevice[device]['parent']]['longitude'] = cymsectiondevice[device]['fromLongitude']
        # add code to figure out if all phase of the devices in each overheadline config are the same
        if device in cymoverheadbyphase.keys():
            thisconfig = cymoverheadbyphase[device]['configuration']
            thisphase = cymsectiondevice[device]['phases']
            if thisconfig not in ohPhasesByConfig.keys():
                ohPhasesByConfig[thisconfig] = thisphase
            else:
                if ohPhasesByConfig[thisconfig] != thisphase:
                    warnings.warn(
                        "Different overhead line phases found for the same overhead line configuration.", RuntimeWarning)


    # Create overhead line conductor dictionaries
    ohl_conds = {}
    for olc in OH_conductors:
        if olc in cymeqconductor.keys():
            if olc not in ohl_conds.keys():
                ohl_conds[olc] = {'object' : 'overhead_line_conductor',
                                'name' : olc,
                                'resistance' : '{:0.6f}'.format(cymeqconductor[olc]['resistance']),
                                'geometric_mean_radius' : '{:0.6f}'.format(cymeqconductor[olc]['geometric_mean_radius'])}
        else:
            print("There is no conductor spec for ", olc, " in the equipment database provided.\n")              
    if 'ohl_conds' in equipments.keys():
        _findUniqueEquipment(ohl_conds, equipments['ohl_conds'])
    else:
        equipments['ohl_conds'] = ohl_conds
    # Create overhead line spacing dictionaries
    ohl_spcs = {}
    for ols in uniqueOhSpacing:
        if ols in cymeqgeometricalarrangement.keys():
            if ols not in ohl_spcs.keys():
                # ohl_spcs[ols] = {}
                name = ols + '_AN'
                ohl_spcs[name] = {'object' : 'line_spacing',
                                  'name' : name,
                                  'distance_AN' : '{:0.6f}'.format(cymeqgeometricalarrangement[ols]['distance_AN']),
                                  'distance_AE' : '{:0.6f}'.format(cymeqgeometricalarrangement[ols]['distance_AE']),
                                  'distance_NE' : '{:0.6f}'.format(cymeqgeometricalarrangement[ols]['distance_NE'])}
                name = ols + '_BN'
                ohl_spcs[name] = {'object' : 'line_spacing',
                                  'name' : name,
                                  'distance_BN' : '{:0.6f}'.format(cymeqgeometricalarrangement[ols]['distance_BN']),
                                  'distance_BE' : '{:0.6f}'.format(cymeqgeometricalarrangement[ols]['distance_BE']),
                                  'distance_NE' : '{:0.6f}'.format(cymeqgeometricalarrangement[ols]['distance_NE'])}
                name = ols + '_CN'
                ohl_spcs[name] = {'object' : 'line_spacing',
                                  'name' : name,
                                  'distance_CN' : '{:0.6f}'.format(cymeqgeometricalarrangement[ols]['distance_CN']),
                                  'distance_CE' : '{:0.6f}'.format(cymeqgeometricalarrangement[ols]['distance_CE']),
                                  'distance_NE' : '{:0.6f}'.format(cymeqgeometricalarrangement[ols]['distance_NE'])}
                name = ols + '_ABN'
                ohl_spcs[name] = {'object' : 'line_spacing',
                                  'name' : name,
                                  'distance_AN' : '{:0.6f}'.format(cymeqgeometricalarrangement[ols]['distance_AN']),
                                  'distance_AE' : '{:0.6f}'.format(cymeqgeometricalarrangement[ols]['distance_AE']),
                                  'distance_BN' : '{:0.6f}'.format(cymeqgeometricalarrangement[ols]['distance_BN']),
                                  'distance_BE' : '{:0.6f}'.format(cymeqgeometricalarrangement[ols]['distance_BE']),
                                  'distance_AB' : '{:0.6f}'.format(cymeqgeometricalarrangement[ols]['distance_AB']),
                                  'distance_NE' : '{:0.6f}'.format(cymeqgeometricalarrangement[ols]['distance_NE'])}
                name = ols + '_ACN'
                ohl_spcs[name] = {'object' : 'line_spacing',
                                  'name' : name,
                                  'distance_AN' : '{:0.6f}'.format(cymeqgeometricalarrangement[ols]['distance_AN']),
                                  'distance_AE' : '{:0.6f}'.format(cymeqgeometricalarrangement[ols]['distance_AE']),
                                  'distance_CN' : '{:0.6f}'.format(cymeqgeometricalarrangement[ols]['distance_CN']),
                                  'distance_CE' : '{:0.6f}'.format(cymeqgeometricalarrangement[ols]['distance_CE']),
                                  'distance_AC' : '{:0.6f}'.format(cymeqgeometricalarrangement[ols]['distance_AC']),
                                  'distance_NE' : '{:0.6f}'.format(cymeqgeometricalarrangement[ols]['distance_NE'])}
                name = ols + '_BCN'
                ohl_spcs[name] = {'object' : 'line_spacing',
                                  'name' : name,
                                  'distance_CN' : '{:0.6f}'.format(cymeqgeometricalarrangement[ols]['distance_CN']),
                                  'distance_CE' : '{:0.6f}'.format(cymeqgeometricalarrangement[ols]['distance_CE']),
                                  'distance_BN' : '{:0.6f}'.format(cymeqgeometricalarrangement[ols]['distance_BN']),
                                  'distance_BE' : '{:0.6f}'.format(cymeqgeometricalarrangement[ols]['distance_BE']),
                                  'distance_BC' : '{:0.6f}'.format(cymeqgeometricalarrangement[ols]['distance_BC']),
                                  'distance_NE' : '{:0.6f}'.format(cymeqgeometricalarrangement[ols]['distance_NE'])}
                name = ols + '_ABCN'
                ohl_spcs[name] = {'object' : 'line_spacing',
                                  'name' : name,
                                  'distance_AN' : '{:0.6f}'.format(cymeqgeometricalarrangement[ols]['distance_AN']),
                                  'distance_AE' : '{:0.6f}'.format(cymeqgeometricalarrangement[ols]['distance_AE']),
                                  'distance_BN' : '{:0.6f}'.format(cymeqgeometricalarrangement[ols]['distance_BN']),
                                  'distance_BE' : '{:0.6f}'.format(cymeqgeometricalarrangement[ols]['distance_BE']),
                                  'distance_AB' : '{:0.6f}'.format(cymeqgeometricalarrangement[ols]['distance_AB']),
                                  'distance_CN' : '{:0.6f}'.format(cymeqgeometricalarrangement[ols]['distance_CN']),
                                  'distance_CE' : '{:0.6f}'.format(cymeqgeometricalarrangement[ols]['distance_CE']),
                                  'distance_AC' : '{:0.6f}'.format(cymeqgeometricalarrangement[ols]['distance_AC']),
                                  'distance_BC' : '{:0.6f}'.format(cymeqgeometricalarrangement[ols]['distance_BC']),
                                  'distance_NE' : '{:0.6f}'.format(cymeqgeometricalarrangement[ols]['distance_NE'])}
        else:
            print("There is no line spacing spec for ", ols, "in the equipment database provided.\n")
    if 'ohl_spcs' in equipments.keys():
        _findUniqueEquipment(ohl_spcs, equipments['ohl_spcs'])
    else:
        equipments['ohl_spcs'] = ohl_spcs
    # Create overhead line configuration dictionaries
    ohl_cfgs = {}
    ohl_neutral = []
    for olcfg in ohConfigurations:
        if olcfg not in ohl_cfgs.keys():
            ohl_cfgs[olcfg] = copy.deepcopy(ohConfigurations[olcfg])
            ohl_cfgs[olcfg]['name'] = olcfg
            ohl_cfgs[olcfg]['object'] = 'line_configuration'
            if olcfg in ohPhasesByConfig:
                thisphase = ohPhasesByConfig[olcfg]
                spacing = ohConfigurations[olcfg]['spacing']
                ohl_cfgs[olcfg]['spacing'] = spacing + '_' + thisphase
            if 'conductor_N' in ohl_cfgs[olcfg].keys():
                ohl_neutral.append(olcfg)
            else:
                # add as comments for neutral conductor that is None.
                ohl_cfgs[olcfg]['// conductor_N'] = 'NONE'
    for olcfg in UOLConfigNames:
        if olcfg in cymeqoverheadlineunbalanced.keys():
            if olcfg not in ohl_cfgs.keys():
                ohl_cfgs[olcfg] = copy.deepcopy(cymeqoverheadlineunbalanced[olcfg])
        else:
            print("There is no overhead line configuration for", olcfg, " in the equipment database provided.")
    if 'ohl_cfgs' in equipments.keys():
        _findUniqueEquipment(ohl_cfgs, equipments['ohl_cfgs'])
    else:
        equipments['ohl_cfgs'] = ohl_cfgs
    # Create overhead line dictionaries
    ohls = {}
    ohls2 = {}
    for ohl in cymsectiondevice.keys():
        if cymsectiondevice[ohl]['device_type'] == 3:
            if ohl not in cymoverheadbyphase.keys():
                print("There is no line spec for ", ohl, " in the network database provided.\n")
            elif ohl not in ohls.keys():
                if ohl not in parallelLinks:
                    # if cymsectiondevice[ohl]['from'] in connectedNodes and cymsectiondevice[ohl]['to'] in connectedNodes:
                    #     print('connected section. from: ' + cymsectiondevice[ohl]['from'] + ', to: ' + cymsectiondevice[ohl]['to'])
                        ohls[ohl] = {'object' : 'overhead_line',
                                    'name' : ohl,
                                    'phases' : cymsectiondevice[ohl]['phases'],
                                    'from' :  cymsectiondevice[ohl]['from'],
                                    'to' :  cymsectiondevice[ohl]['to'],
                                    'length' :  '{:0.6f}'.format(cymoverheadbyphase[ohl]['length']),
                                    'configuration' : cymoverheadbyphase[ohl]['configuration']}
                    # else:
                    #     ohls[ohl] = {'// object' : 'overhead_line',
                    #                 '// name' : ohl,
                    #                 '// phases' : cymsectiondevice[ohl]['phases'],
                    #                 '// from' :  cymsectiondevice[ohl]['from'],
                    #                 '// to' :  cymsectiondevice[ohl]['to'],
                    #                 '// length' :  '{:0.6f}'.format(cymoverheadbyphase[ohl]['length']),
                    #                 '// configuration' : cymoverheadbyphase[ohl]['configuration']}
                else: 
                    ohls[ohl + 'par1'] = {'object' : 'overhead_line',
                                            'name' : ohl + 'par1',
                                            'phases' : cymsectiondevice[ohl]['phases'],
                                            'from' :  cymsectiondevice[ohl]['from'],
                                            'to' :  ohl + 'parNode',
                                            'length' :  '{:0.6f}'.format(cymoverheadbyphase[ohl]['length']/2.0),
                                            'configuration' : cymoverheadbyphase[ohl]['configuration']}
                    ohls[ohl + 'par2'] = {'object' : 'overhead_line',
                                            'name' : ohl + 'par2',
                                            'phases' : cymsectiondevice[ohl]['phases'],
                                            'from' :  ohl + 'parNode',
                                            'to' :  cymsectiondevice[ohl]['to'],
                                            'length' :  '{:0.6f}'.format(cymoverheadbyphase[ohl]['length']/2.0),
                                            'configuration' : cymoverheadbyphase[ohl]['configuration']}
                    nodes[ohl + 'parNode'] = {'object' : 'node',
                                            'name' : ohl + 'parNode',
                                            'phases' : cymsectiondevice[ohl]['phases'],
                                            'nominal_voltage' : str(feeder_VLN),
                                            'latitude' : str((float(cymsectiondevice[ohl]['fromLatitude']) + float(cymsectiondevice[ohl]['toLatitude']))/2.0),
                                            'longitude' : str((float(cymsectiondevice[ohl]['fromLongitude']) + float(cymsectiondevice[ohl]['toLongitude']))/2.0)}
        elif cymsectiondevice[ohl]['device_type'] == 23:
            if ohl not in cymUnbalancedOverheadLine.keys():
                print("There is no line spec for ", ohl, " in the network database provided.\n")
            elif ohl not in ohls.keys():
                if ohl not in parallelLinks:
                    ohls[ohl] = {'object' : 'overhead_line',
                                'name' : ohl,
                                'phases' : cymsectiondevice[ohl]['phases'],
                                'from' :  cymsectiondevice[ohl]['from'],
                                'to' :  cymsectiondevice[ohl]['to'],
                                'length' :  '{:0.6f}'.format(cymUnbalancedOverheadLine[ohl]['length']),
                                'configuration' : cymUnbalancedOverheadLine[ohl]['configuration']}
                else: 
                    ohls[ohl + 'par1'] = {'object' : 'overhead_line',
                                            'name' : ohl + 'par1',
                                            'phases' : cymsectiondevice[ohl]['phases'],
                                            'from' :  cymsectiondevice[ohl]['from'],
                                            'to' :  ohl + 'parNode',
                                            'length' :  '{:0.6f}'.format(cymUnbalancedOverheadLine[ohl]['length']/2.0),
                                            'configuration' : cymUnbalancedOverheadLine[ohl]['configuration']}
                    ohls[ohl + 'par2'] = {'object' : 'overhead_line',
                                            'name' : ohl + 'par2',
                                            'phases' : cymsectiondevice[ohl]['phases'],
                                            'from' :  ohl + 'parNode',
                                            'to' :  cymsectiondevice[ohl]['to'],
                                            'length' :  '{:0.6f}'.format(cymUnbalancedOverheadLine[ohl]['length']/2.0),
                                            'configuration' : cymUnbalancedOverheadLine[ohl]['configuration']}
                    nodes[ohl + 'parNode'] = {'object' : 'node',
                                                'name' : ohl + 'parNode',
                                                'phases' : cymsectiondevice[ohl]['phases'],
                                                'nominal_voltage' : str(feeder_VLN),
                                                'latitude' : str((float(cymsectiondevice[ohl]['fromLatitude']) + float(cymsectiondevice[ohl]['toLatitude']))/2.0),
                                                'longitude' : str((float(cymsectiondevice[ohl]['fromLongitude']) + float(cymsectiondevice[ohl]['toLongitude']))/2.0)}
        elif  cymsectiondevice[ohl]['device_type'] == 2:
            if ohl not in ohl_type2.keys():
                print("There is no line spec for ", ohl, " in the network database provided.\n")
            elif ohl not in ohls2.keys():
                if ohl not in parallelLinks:
                    # if cymsectiondevice[ohl]['from'] in connectedNodes and cymsectiondevice[ohl]['to'] in connectedNodes:
                    #     print('connected section. from: ' + cymsectiondevice[ohl]['from'] + ', to: ' + cymsectiondevice[ohl]['to'])
                        ohls2[ohl] = {'object' : 'overhead_line',
                                    'name' : ohl,
                                    'phases' : cymsectiondevice[ohl]['phases'],
                                    'from' :  cymsectiondevice[ohl]['from'],
                                    'to' :  cymsectiondevice[ohl]['to'],
                                    'length' :  '{:0.6f}'.format(ohl_type2[ohl]['length']),
                                    'configuration' : ohl_type2[ohl]['configuration']}
                    # else:
                    #     ohls2[ohl] = {'// object' : 'overhead_line',
                    #                 '// name' : ohl,
                    #                 '// phases' : cymsectiondevice[ohl]['phases'],
                    #                 '// from' :  cymsectiondevice[ohl]['from'],
                    #                 '// to' :  cymsectiondevice[ohl]['to'],
                    #                 '// length' :  '{:0.6f}'.format(ohl_type2[ohl]['length']),
                    #                 '// configuration' : ohl_type2[ohl]['configuration']}
                else:
                    ohls2[ohl + 'par1'] = {'object' : 'overhead_line',
                                            'name' : ohl + 'par1',
                                            'phases' : cymsectiondevice[ohl]['phases'],
                                            'from' :  cymsectiondevice[ohl]['from'],
                                            'to' :  ohl + 'parNode',
                                            'length' :  '{:0.6f}'.format(ohl_type2[ohl]['length']/2.0),
                                            'configuration' : ohl_type2[ohl]['configuration']}
                    ohls2[ohl + 'par2'] = {'object' : 'overhead_line',
                                            'name' : ohl + 'par2',
                                            'phases' : cymsectiondevice[ohl]['phases'],
                                            'from' :  ohl + 'parNode',
                                            'to' :  cymsectiondevice[ohl]['to'],
                                            'length' :  '{:0.6f}'.format(ohl_type2[ohl]['length']/2.0),
                                            'configuration' : ohl_type2[ohl]['configuration']}
                    nodes[ohl + 'parNode'] = {'object' : 'node',
                                            'name' : ohl + 'parNode',
                                            'phases' : cymsectiondevice[ohl]['phases'],
                                            'nominal_voltage' : str(feeder_VLN),
                                            'latitude' : str((float(ohl_type2[ohl]['fromLatitude']) + float(ohl_type2[ohl]['toLatitude']))/2.0),
                                            'longitude' : str((float(ohl_type2[ohl]['fromLongitude']) + float(ohl_type2[ohl]['toLongitude']))/2.0)}
                
    # Create underground line conductor, and spacing dictionaries
    ugl_conds = {}
    ugl_sps = {}
    for ulc in UG_conductors:
        if ulc in cymcsvundergroundcable.keys():
            if ulc + 'cond' not in ugl_conds.keys():
                ugl_conds[ulc + 'cond'] = {'object' : 'underground_line_conductor',
                                            'name' : ulc + 'cond',
                                            'conductor_resistance' : cymcsvundergroundcable[ulc]['conductor_resistance'],
                                            'neutral_gmr' : cymcsvundergroundcable[ulc]['neutral_gmr'],
                                            'outer_diameter' : cymcsvundergroundcable[ulc]['outer_diameter'],
                                            'neutral_strands' : cymcsvundergroundcable[ulc]['neutral_strands'],
                                            'neutral_resistance' : cymcsvundergroundcable[ulc]['neutral_resistance'],
                                            'neutral_diameter' : cymcsvundergroundcable[ulc]['neutral_diameter'],
                                            'conductor_diameter' : cymcsvundergroundcable[ulc]['conductor_diameter'],
                                            'conductor_gmr' : cymcsvundergroundcable[ulc]['conductor_gmr']}
                if ulc + 'sps' not in ugl_sps.keys():
                    ugl_sps[ulc + 'sps'] = {'object' : 'line_spacing',
                                            'name' : ulc + 'sps',
                                            'distance_AB' : cymcsvundergroundcable[ulc]['distance_AB'],
                                            'distance_AC' : cymcsvundergroundcable[ulc]['distance_AC'],
                                            'distance_BC' : cymcsvundergroundcable[ulc]['distance_BC']}
        else:
            print("Runtimerror: No configuratino spec for {:s} in the underground csv file provided.", ulc)
    if 'ugl_conds' in equipments.keys():
        _findUniqueEquipment(ugl_conds, equipments['ugl_conds'])
    else:
        equipments['ugl_conds'] = ugl_conds
    if 'ugl_sps' in equipments.keys():
        _findUniqueEquipment(ugl_sps, equipments['ugl_sps'])
    else:
        equipments['ugl_sps'] = ugl_sps
    # Creat Underground line configuration, and link objects.
    ugl_cfgs = {}
    ugls = {}
    uglcfgDict = _readUndergroundCableGLMFile(ugConfig)
    for ugl in cymsectiondevice.keys():
        if cymsectiondevice[ugl]['device_type'] == 1:
            ph = cymsectiondevice[ugl]['phases'].replace('N', '')
            if ugl not in cymundergroundline.keys():
                print("There is no line spec for ", ugl, " in the network database provided.\n")
            else:
                phs = 0
                if 'A' in ph:
                    phs += 1
                if 'B' in ph:
                    phs += 2
                if 'C' in ph:
                    phs += 4
                if phs == 1:
                    config_name = cymundergroundline[ugl]['cable_id'] + 'phA'
                elif phs == 2:
                    config_name = cymundergroundline[ugl]['cable_id'] + 'phB'
                elif phs == 4:
                    config_name = cymundergroundline[ugl]['cable_id'] + 'phC'
                elif phs == 3:
                    config_name = cymundergroundline[ugl]['cable_id'] + 'phAB'
                elif phs == 5:
                    config_name = cymundergroundline[ugl]['cable_id'] + 'phAC'
                elif phs == 6:
                    config_name = cymundergroundline[ugl]['cable_id'] + 'phBC'
                elif phs == 7:
                    config_name = cymundergroundline[ugl]['cable_id'] + 'phABC'
                if config_name not in ugl_cfgs.keys():
                    if config_name in uglcfgDict:
                        thisUglcfgDict = uglcfgDict[config_name]
                        ugl_cfgs[config_name] = {'object' : 'line_configuration',
                                                   'name' : config_name}
                        for key, value in thisUglcfgDict.items():
                            # if key[0] == 'c':
                            #     pass
                            # else:
                            ugl_cfgs[config_name][key] = value
                    else:
                        print(config_name + ' not found in supplemental underground cable configuration glm file.')
                    #                                             'spacing' : cymundergroundline[ugl]['cable_id'] + 'sps'}
                    # for phase in ph:
                    #     if phase != 'D' or phase != 'N':
                    #         ugl_cfgs[config_name]['conductor_{:s}'.format(phase)] = cymundergroundline[ugl]['cable_id'] + 'cond'
                if ugl not in ugls.keys():
                    if ugl not in parallelLinks:
                        # if cymsectiondevice[ugl]['from'] in connectedNodes and cymsectiondevice[ugl]['to'] in connectedNodes:
                        #     print('connected section. from: ' + cymsectiondevice[ugl]['from'] + ', to: ' + cymsectiondevice[ugl]['to'])
                            ugls[ugl] = {'object' : 'underground_line',
                                        'name' : ugl,
                                        'phases' : cymsectiondevice[ugl]['phases'],
                                        'from' :  cymsectiondevice[ugl]['from'],
                                        'to' :  cymsectiondevice[ugl]['to'],
                                        'length' :  '{:0.6f}'.format(cymundergroundline[ugl]['length']),
                                        'configuration' : config_name}
                        # else:
                        #     ugls[ugl] = {'// object' : 'underground_line',
                        #                 '// name' : ugl,
                        #                 '// phases' : cymsectiondevice[ugl]['phases'],
                        #                 '// from' :  cymsectiondevice[ugl]['from'],
                        #                 '// to' :  cymsectiondevice[ugl]['to'],
                        #                 '// length' :  '{:0.6f}'.format(cymundergroundline[ugl]['length']),
                        #                 '// configuration' : config_name}
                    else:
                        ugls[ugl + 'par1'] = {'object' : 'underground_line',
                                                'name' : ugl + 'par1',
                                                'phases' : cymsectiondevice[ugl]['phases'],
                                                'from' :  cymsectiondevice[ugl]['from'],
                                                'to' :  ugl + 'parNode',
                                                'length' :  '{:0.6f}'.format(cymundergroundline[ugl]['length']/2.0),
                                                'configuration' : config_name}
                        ugls[ugl + 'par2'] = {'object' : 'underground_line',
                                                'name' : ugl + 'par2',
                                                'phases' : cymsectiondevice[ugl]['phases'],
                                                'from' :  ugl + 'parNode',
                                                'to' :  cymsectiondevice[ugl]['to'],
                                                'length' :  '{:0.6f}'.format(cymundergroundline[ugl]['length']/2.0),
                                                'configuration' : config_name}
                        nodes[ugl + 'parNode'] = {'object' : 'node',
                                                'name' : ugl + 'parNode',
                                                'phases' : cymsectiondevice[ugl]['phases'],
                                                'nominal_voltage' : str(feeder_VLN),
                                                'latitude' : str((float(cymsectiondevice[ugl]['fromX']) + float(cymsectiondevice[ugl]['toX']))/2.0),
                                                'longitude' : str((float(cymsectiondevice[ugl]['fromY']) + float(cymsectiondevice[ugl]['toY']))/2.0)}
    if 'ugl_cfgs' in equipments.keys():
        _findUniqueEquipment(ugl_cfgs, equipments['ugl_cfgs'])
    else:
        equipments['ugl_cfgs'] = ugl_cfgs
    # Create switch dictionaries
    swObjs = {}
    for swObj in cymsectiondevice.keys():
        if cymsectiondevice[swObj]['device_type'] == 13:
            if swObj not in cymswitch.keys():
                print("There is no switch spec for  ", swObj, " in the network database provided.\n")  
            elif swObj not in swObjs.keys():
                # if cymsectiondevice[swObj]['from'] in connectedNodes and cymsectiondevice[swObj]['to'] in connectedNodes:
                #     print('connected section. from: ' + cymsectiondevice[swObj]['from'] + ', to: ' + cymsectiondevice[swObj]['to'])
                    swObjs[swObj] = {'object' : 'switch',
                                    'name' : swObj,
                                    'phases' : cymsectiondevice[swObj]['phases'].replace('N', ''),
                                    'from' : cymsectiondevice[swObj]['from'],
                                    'to' : cymsectiondevice[swObj]['to'],
                                    'operating_mode' : 'BANKED'}
                    # table CYMSWITCH, column ClosedPhase, 0 is OPEN, 1,2,7 are CLOSED
                    if cymswitch[swObj]['status'] == 0:
                        status = 'OPEN'
                    else:
                        status = 'CLOSED'
                    for phase in swObjs[swObj]['phases']:
                        swObjs[swObj]['phase_{:s}_state'.format(phase)] = status
                # else:
                #     swObjs[swObj] = {'// object' : 'switch',
                #                     '// name' : swObj,
                #                     '// phases' : cymsectiondevice[swObj]['phases'].replace('N', ''),
                #                     '// from' : cymsectiondevice[swObj]['from'],
                #                     '// to' : cymsectiondevice[swObj]['to'],
                #                     '// operating_mode' : 'BANKED'}
                #     # table CYMSWITCH, column ClosedPhase, 0 is OPEN, 1,2,7 are CLOSED
                #     if cymswitch[swObj]['status'] == 0:
                #         status = 'OPEN'
                #     else:
                #         status = 'CLOSED'
                #     for phase in swObjs[swObj]['// phases']:
                #         swObjs[swObj]['// phase_{:s}_state'.format(phase)] = status
    # Create recloser dictionaries
    rcls = {}
    for rcl in cymsectiondevice.keys():
        if cymsectiondevice[rcl]['device_type'] == 10:
            if rcl not in list(cymrecloser.keys()):
                print("There is no recloster spec for ", rcl, " in the network database provided.\n")
            elif rcl not in list(rcls.keys()):
                rcls[rcl] = {'object' : 'recloser',
                                        'name' : rcl,
                                        'phases' : cymsectiondevice[rcl]['phases'].replace('N', ''),
                                        'from' : cymsectiondevice[rcl]['from'],
                                        'to' : cymsectiondevice[rcl]['to'],
                                        'operating_mode' : 'BANKED'}
                if cymrecloser[rcl]['status'] == 0:
                    status = 'CLOSED'
                else:
                    status = 'CLOSED'
                for phase in rcls[rcl]['phases']:
                    rcls[rcl]['phase_{:s}_state'.format(phase)] = status
    # Create sectionalizer dictionaries
    sxnlrs = {}
    for sxnlr in cymsectiondevice.keys():
        if cymsectiondevice[sxnlr]['device_type'] == 12:
            if sxnlr not in list(cymsectionalizer.keys()):
                print("There is no sectionalizer spec for ", sxnlr, " in the network database provided.\n")  
            elif sxnlr not in list(sxnlrs.keys()):
                sxnlrs[sxnlr] = {'object' : 'sectionalizer',
                                'name' : sxnlr,
                                'phases' : cymsectiondevice[sxnlr]['phases'].replace('N', ''),
                                'from' : cymsectiondevice[sxnlr]['from'],
                                'to' : cymsectiondevice[sxnlr]['to'],
                                'operating_mode' : 'BANKED'}
                if cymsectionalizer[sxnlr]['status'] == 0:
                    status = 'CLOSED'
                else:
                    status = 'CLOSED'
                for phase in sxnlrs[sxnlr]['phases']:
                    sxnlrs[sxnlr]['phase_{:s}_state'.format(phase)] = status
    # Create fuse dictionaries
    fuses = {}
    for fuse in cymsectiondevice.keys():
        if cymsectiondevice[fuse]['device_type'] == 14:
            if fuse not in list(cymfuse.keys()):
                print("There is no fuse spec for ", fuse, " in the network database provided.\n")                
            elif fuse not in list(fuses.keys()):
                # if cymsectiondevice[fuse]['from'] in connectedNodes or cymsectiondevice[fuse]['to'] in connectedNodes:
                #     print('connected section. from: ' + cymsectiondevice[fuse]['from'] + ', to: ' + cymsectiondevice[fuse]['to'])
                    fuses[fuse] = {'object' : 'fuse',
                                    'name' : fuse,
                                    'phases' : cymsectiondevice[fuse]['phases'].replace('N', ''),
                                    'from' : cymsectiondevice[fuse]['from'],
                                    'to' : cymsectiondevice[fuse]['to'],
                                    'repair_dist_type' : 'EXPONENTIAL',
                                    'mean_replacement_time' : '3600',
                                    'current_limit' : '9999'}
                    if cymfuse[fuse]['status'] == 0:
                        status = 'GOOD'
                    else:
                        status = 'GOOD'
                    for phase in fuses[fuse]['phases']:
                        fuses[fuse]['phase_{:s}_status'.format(phase)] = status
                # else:
                #     fuses[fuse] = {'// object' : 'fuse',
                #                     '// name' : fuse,
                #                     '// phases' : cymsectiondevice[fuse]['phases'].replace('N', ''),
                #                     '// from' : cymsectiondevice[fuse]['from'],
                #                     '// to' : cymsectiondevice[fuse]['to'],
                #                     '// repair_dist_type' : 'EXPONENTIAL',
                #                     '// mean_replacement_time' : '3600',
                #                     '// current_limit' : '9999'}
                #     if cymfuse[fuse]['status'] == 0:
                #         status = 'GOOD'
                #     else:
                #         status = 'GOOD'
                #     for phase in fuses[fuse]['// phases']:
                #         fuses[fuse]['// phase_{:s}_status'.format(phase)] = status
    # Create capacitor dictionaries
    caps = {}
    for cap in cymsectiondevice.keys():
        if cymsectiondevice[cap]['device_type'] == 17:
            if cap not in list(cymshuntcapacitor.keys()):               
                print("There is no capacitor spec for ", cap, " in the network database provided.\n")
            elif cap not in list(caps.keys()):
                caps[cap] = {'object' : 'capacitor',
                            'name' : cap,
                            'phases' : cymsectiondevice[cap]['phases'],
                            'parent' : cymsectiondevice[cap]['parent'],
                            'control_level' : 'INDIVIDUAL',
                            'control' : 'MANUAL',
                            'cap_nominal_voltage' : str(feeder_VLN),
                            # 'time_delay' : '2',
                            # 'dwell_time' : '3',
                            'latitude' : str(float(nodes[cymsectiondevice[cap]['parent']]['latitude']) + random.uniform(-5, 5)),
                            'longitude' : str(float(nodes[cymsectiondevice[cap]['parent']]['longitude']) + random.uniform(-5, 5))}
                if cymshuntcapacitor[cap]['status'] == 0:
                    status = 'CLOSED'
                else:
                    status = 'OPEN'
                for phase in caps[cap]['phases']:
                    if phase not in ['N', 'D']:
                        caps[cap]['switch{:s}'.format(phase)] = status
                        caps[cap]['capacitor_{:s}'.format(phase)] = str(cymshuntcapacitor[cap]['capacitor_{:s}'.format(phase)])
    # Create load dictionaries
    loads = {}
    spct_cfgs ={}
    spcts = {}
    tpns = {}
    tpms = {}
    for load in cymsectiondevice.keys():
        if cymsectiondevice[load]['device_type'] == 20:
            if load not in cymcustomerload.keys():
                print("There is no load spec for ", load, " in the network database provided.\n")
            elif primaryLoadOnly:
                if load not in loads.keys():
                    loads[load] = {'object' : 'load',
                                    'name' : load,
                                    'phases' : cymsectiondevice[load]['phases'],
                                    'parent' : cymsectiondevice[load]['parent'],
                                    'nominal_voltage' : str(feeder_VLN)}
                                    # 'load_class' : 'C'}
                    for phase in loads[load]['phases']:
                        if phase not in ['N','D']:
                            if load in deltaConnections[feeder_id]:
                                print('found delta load configuation')
                                if phase == 'A':
                                    loads[load]['constant_power_AB'] = cymcustomerload[load]['constant_power_{:s}'.format(phase)]
                                if phase == 'B':
                                    loads[load]['constant_power_BC'] = cymcustomerload[load]['constant_power_{:s}'.format(phase)]
                                if phase == 'C':
                                    loads[load]['constant_power_CA'] = cymcustomerload[load]['constant_power_{:s}'.format(phase)]
                            else:
                                loads[load]['constant_power_{:s}'.format(phase)] = cymcustomerload[load]['constant_power_{:s}'.format(phase)]
            else:
                if load not in loads.keys() and cymcustomerload[load]['load_class'] == 'C':
                # elif load not in loads.keys():
                    loads[load] = {'object' : 'load',
                                    'name' : load,
                                    'phases' : cymsectiondevice[load]['phases'],
                                    'parent' : cymsectiondevice[load]['parent'],
                                    'nominal_voltage' : str(feeder_VLN)}
                                    # 'load_class' : 'C'}
                    for phase in loads[load]['phases']:
                        if phase not in ['N','D']:
                            loads[load]['constant_power_{:s}'.format(phase)] = cymcustomerload[load]['constant_power_{:s}'.format(phase)]
                elif load not in list(tpns.keys()):
                    for phase in cymsectiondevice[load]['phases']:
                        #print load
                        #print phase
                        if phase not in ['N', 'D']:
                            if cymcustomerload[load]['constant_power_{:s}'.format(phase)] != None:
                                spctRating = _find_SPCT_rating(cymcustomerload[load]['constant_power_{:s}'.format(phase)])

                                spct_cfgs['SPCTconfig{:s}{:s}'.format(load, phase)] = { 'object' : 'transformer_configuration',
                                                                                        'name' : 'SPCTconfig{:s}{:s}'.format(load, phase),
                                                                                        'connect_type' : 'SINGLE_PHASE_CENTER_TAPPED',
                                                                                        'install_type' : 'POLETOP',
                                                                                        'primary_voltage' : str(feeder_VLN),
                                                                                        'secondary_voltage' : '120',
                                                                                        'power_rating' : spctRating,
                                                                                        'power{:s}_rating'.format(phase) : spctRating,
                                                                                        'impedance' : '0.00033+0.0022j'}
                                spcts['SPCT{:s}{:s}'.format(load, phase)] = { 'object' : 'transformer',
                                                                                'name' : 'SPCT{:s}{:s}'.format(load, phase),
                                                                                'phases' : '{:s}S'.format(phase),
                                                                                'from' : cymsectiondevice[load]['parent'],
                                                                                'to' : 'tpm{:s}{:s}'.format(load, phase),
                                                                                'configuration' : 'SPCTconfig{:s}{:s}'.format(load, phase)}
                                tpms['tpm{:s}{:s}'.format(load, phase)] = {'object' : 'triplex_meter',
                                                                            'name' : 'tpm{:s}{:s}'.format(load, phase),
                                                                            'phases' : '{:s}S'.format(phase),
                                                                            'nominal_voltage' : '120',
                                                                            'latitude' : str(float(nodes[cymsectiondevice[load]['parent']]['latitude']) + random.uniform(-5, 5)),
                                                                            'longitude' : str(float(nodes[cymsectiondevice[load]['parent']]['longitude']) + random.uniform(-5, 5))}
                                tpns['tpn{:s}{:s}'.format(load, phase)] = {'object' : 'triplex_node',
                                                                            'name' : 'tpn{:s}{:s}'.format(load, phase),
                                                                            'phases' : '{:s}S'.format(phase),
                                                                            'nominal_voltage' : '120',
                                                                            'parent' : 'tpm{:s}{:s}'.format(load, phase),
                                                                            'power_12' : cymcustomerload[load]['constant_power_{:s}'.format(phase)],
                                                                            'latitude' : str(float(tpms['tpm{:s}{:s}'.format(load, phase)]['latitude']) + random.uniform(-5, 5)),
                                                                            'longitude' : str(float(tpms['tpm{:s}{:s}'.format(load, phase)]['longitude']) + random.uniform(-5, 5))}
    if 'spct_cfgs' in equipments.keys():
        _findUniqueEquipment(spct_cfgs, equipments['spct_cfgs'])
    else:
        equipments['spct_cfgs'] = spct_cfgs
    # Create regulator and regulator configuration dictionaries
    reg_cfgs = {}
    regs = {}
    for reg in cymsectiondevice.keys():
        if cymsectiondevice[reg]['device_type'] == 4:
            if reg not in list(cymregulator.keys()):
                print("There is no regulator spec for ", reg, " in the network database provided.\n")                
            else:
                regEq = cymregulator[reg]['equipment_name']
                if regEq == reg:
                    suffix = 'cfg'
                else:
                    suffix = ''
                if regEq not in list(cymeqregulator.keys()):
                    raiseTaps = '16'
                    lowerTaps = '16'
                else:
                    raiseTaps = cymeqregulator[regEq]['raise_taps']
                    lowerTaps = cymeqregulator[regEq]['lower_taps']
                ph = cymsectiondevice[reg]['phases'].replace('N', '')
                if regEq not in list(reg_cfgs.keys()):
                    reg_cfgs[regEq] = {'object' : 'regulator_configuration',
                                        'name' : regEq + suffix,
                                        'connect_type' : 'WYE_WYE',
                                        'band_center' : str(feeder_VLN),
                                        # 'band_width' : str(cymregulator[reg]['band_width']),
                                        'regulation' : str(cymregulator[reg]['regulation']),
                                        'time_delay' : '30.0',
                                        'dwell_time' : '5',
                                        'Control' : 'OUTPUT_VOLTAGE',
                                        'control_level' : 'INDIVIDUAL',
                                        'raise_taps' : raiseTaps,
                                        'lower_taps' : lowerTaps,
                                        'band_width' : 2.0}
                    for phase in ph:
                        reg_cfgs[regEq]['tap_pos_{:s}'.format(phase)] = str(cymregulator[reg]['tap_pos_{:s}'.format(phase)])
                if reg not in list(reg_cfgs.keys()):
                    regs[reg] = {'object' : 'regulator',
                                'name' : reg,
                                'phases' : ph,
                                'from' : cymsectiondevice[reg]['from'],
                                'to' : cymsectiondevice[reg]['to'],
                                'configuration' : regEq + suffix}
    if 'reg_cfgs' in equipments.keys():
        _findUniqueEquipment(reg_cfgs, equipments['reg_cfgs'])
    else:
        equipments['reg_cfgs'] = reg_cfgs
    # Create transformer and transformer configuration dictionaries
    xfmr_cfgs = {}
    xfmrs = {}
    for xfmr in cymsectiondevice.keys():
        if cymsectiondevice[xfmr]['device_type'] == 47:
            if xfmr not in list(cymxfmr.keys()):
                print("There is no xmfr spec for ", xfmr, " in the network database provided.\n")
            else:
                xfmrEq = cymxfmr[xfmr]['equipment_name']
                if xfmrEq == xfmr:
                    suffix = 'cfg'
                else:
                    suffix = ''
                ph = cymsectiondevice[xfmr]['phases'].replace('N', '')
                phNum = 0
                if 'A' in ph:
                    phNum += 1.0
                if 'B' in ph:
                    phNum += 1.0
                if 'C' in ph:
                    phNum += 1.0
                if xfmrEq not in cymeqautoxfmr.keys():
                    print("There is no xmfr spec for ", xfmrEq, " in the network database provided.\n")
                else:
                    if xfmrEq not in list(xfmr_cfgs.keys()):
                        xfmr_cfgs[xfmrEq] = {'object' : 'transformer_configuration',
                                            'name' : xfmrEq + suffix,
                                            'connect_type' : 'WYE_WYE',
                                            'primary_voltage' : '{:0.6f}'.format(cymeqautoxfmr[xfmrEq]['PrimaryVoltage']),
                                            'secondary_voltage' : '{:0.6f}'.format(cymeqautoxfmr[xfmrEq]['SecondaryVoltage']),
                                            'impedance' : cymeqautoxfmr[xfmrEq]['impedance'],
                                            'power_rating' : '{:0.0f}'.format(cymeqautoxfmr[xfmrEq]['PrimaryRatedCapacity'])}
                        for phase in ph:
                            if phase not in ['N', 'D']:
                                xfmr_cfgs[xfmrEq]['power{:s}_rating'.format(phase)] = '{:0.6f}'.format(cymeqautoxfmr[xfmrEq]['PrimaryRatedCapacity']/phNum)
                if xfmr not in list(xfmrs.keys()):
                    xfmrs[xfmr] = {'object' : 'transformer',
                                            'name' : xfmr,
                                            'phases' : ph,
                                            'from' : cymsectiondevice[xfmr]['from'],
                                            'to' : cymsectiondevice[xfmr]['to'],
                                            'configuration' : xfmrEq + suffix}
        elif cymsectiondevice[xfmr]['device_type'] == 48:
            if xfmr not in list(cym3wxfmr.keys()):
                print("There is no xfmr spec for ", xfmr, " in the network database provided.\n")
            else:
                xfmrEq = cym3wxfmr[xfmr]['equipment_name']
                if xfmrEq == xfmr:
                    suffix = 'cfg'
                else:
                    suffix = ''
                ph = cymsectiondevice[xfmr]['phases'].replace('N', '')
                phNum = 0
                if 'A' in ph:
                    phNum += 1.0
                if 'B' in ph:
                    phNum += 1.0
                if 'C' in ph:
                    phNum += 1.0
                if xfmrEq not in list(cymeq3wautoxfmr.keys()):
                    print("There is no xfmr spec for ", xfmrEq, " in the network database provided.\n")                    
                else:
                    if xfmrEq not in list(xfmr_cfgs.keys()):
                        xfmr_cfgs[xfmrEq] = {'object' : 'transformer_configuration',
                                                            'name' : xfmrEq + suffix,
                                                            'connect_type' : 'WYE_WYE',
                                                            'primary_voltage' : '{:0.6f}'.format(cymeq3wautoxfmr[xfmrEq]['PrimaryVoltage']),
                                                            'secondary_voltage' : '{:0.6f}'.format(cymeq3wautoxfmr[xfmrEq]['SecondaryVoltage']),
                                                            'impedance' : cymeq3wautoxfmr[xfmrEq]['impedance'],
                                                            'power_rating' : '{:0.0f}'.format(cymeq3wautoxfmr[xfmrEq]['PrimaryRatedCapacity'])}
                        for phase in ph:
                            if phase not in ['N', 'D']:
                                xfmr_cfgs[xfmrEq]['power{:s}_rating'.format(phase)] = '{:0.6f}'.format(cymeq3wautoxfmr[xfmrEq]['PrimaryRatedCapacity']/phNum)
                if xfmr not in list(xfmrs.keys()):
                    xfmrs[xfmr] = {'object' : 'transformer',
                                            'name' : xfmr,
                                            'phases' : ph,
                                            'from' : cymsectiondevice[xfmr]['from'],
                                            'to' : cymsectiondevice[xfmr]['to'],
                                            'configuration' : xfmrEq + suffix}
    if 'xfmr_cfgs' in equipments.keys():
        _findUniqueEquipment(xfmr_cfgs, equipments['xfmr_cfgs'])
    else:
        equipments['xfmr_cfgs'] = xfmr_cfgs
    #Add dictionaries to feeder tree object
    genericHeaders = [  {"timezone":"PST+8PDT","stoptime":"'2000-01-01 00:00:60'","starttime":"'2000-01-01 00:00:00'","clock":"clock"},
                        #{"omftype":"#set","argument":"minimum_timestep=60"},
                        #{"omftype":"#set","argument":"profiler=1"},
                        {"omftype":"#set","argument":"relax_naming_rules=1"},
                        {"omftype":"module","argument":"generators"},
                        {"omftype":"module","argument":"tape"},
                        {"module":"residential","implicit_enduses":"NONE"},
                        {"solver_method":"NR","NR_iteration_limit":"50","module":"powerflow","line_capacitance":"false"}]
    # add additional header here
    genericHeaders.append({"omftype":"module","argument":"assert"})
    genericHeaders.append({"module":"reliability","report_event_log":"false"})
    for headId in range(len(genericHeaders)):
        header_glmTree[headId] = genericHeaders[headId]
    key = len(header_glmTree)
    # add objects here that is considered as header to be copied over
    fault_check={ 'object' : 'fault_check',
                    'name' : 'base_fault_check_object',
                    'check_mode' : 'ONCHANGE',
                    'strictly_radial' : 'false',
                    'eventgen_object' : 'testgendev',
                    'grid_association' : 'true'}
    eventgen = {}
    voltdump = {}
    # headerList = [{'key':fault_check}]
    header_glmTree[key] = fault_check
    equipmentList = [ohl_conds, ugl_conds, ohl_spcs, ugl_sps, ohl_cfgs, ugl_cfgs, xfmr_cfgs, spct_cfgs, reg_cfgs]
    networkList = [meters, nodes, tpms, tpns, ohls, ugls, xfmrs, spcts, regs, swObjs, rcls, sxnlrs, fuses]
    key = 0
    for objDict in networkList:
        if len(objDict) > 0:
            for obj in objDict.keys():
                network_glmTree[key] = copy.deepcopy(objDict[obj])
                key = len(network_glmTree)
    # Find and fix duplicate names between nodes and links
    _fixdupname(network_glmTree)
    # FINISHED CONVERSION FROM THE DATABASES****************************************************************************************************************************************************   
    for key in network_glmTree.keys():
        if 'object' in network_glmTree[key].keys() and network_glmTree[key]['object'] in ['overhead_line','underground_line','regulator','transformer','switch','fuse'] and ('to' not in network_glmTree[key].keys() or 'from' not in network_glmTree[key].keys()):
            del network_glmTree[key]
    # Create list of all from and to node names
    LinkedNodes = {}
    toNodes = []
    fromNodes = []
    for key in network_glmTree.keys():
        if 'to' in network_glmTree[key].keys():
            ph = LinkedNodes.get(network_glmTree[key]['from'], '')
            LinkedNodes[network_glmTree[key]['from']] = ph + network_glmTree[key]['phases']
            ph = LinkedNodes.get(network_glmTree[key]['to'], '')
            LinkedNodes[network_glmTree[key]['to']] = ph + network_glmTree[key]['phases']
            if network_glmTree[key]['to'] not in toNodes:
                toNodes.append(network_glmTree[key]['to'])
            if network_glmTree[key]['from'] not in fromNodes:
                fromNodes.append(network_glmTree[key]['from'])
    for node in fromNodes:
        if node not in toNodes and node != 'n' + swingBus:
            print(node)
    # Find the unique phase information and place them in the node like object dictionaries
    for node in LinkedNodes.keys():
        phase = ''
        ABCphases = 0
        if 'A' in LinkedNodes[node]:
            phase = phase + 'A'
            ABCphases = ABCphases + 1
        if 'B' in LinkedNodes[node]:
            phase = phase + 'B'
            ABCphases = ABCphases + 1
        if 'C' in LinkedNodes[node]:
            phase = phase + 'C'
            ABCphases = ABCphases + 1
        if 'S' in LinkedNodes[node] and ABCphases == 1 and node not in fromNodes:
            phase = phase + 'S'
        else:
            phase = phase + 'N'
            
        for x in network_glmTree.keys():
            if 'name' in network_glmTree[x].keys() and network_glmTree[x]['name'] == node:
                network_glmTree[x]['phases'] = phase
    
    # Take care of open switches
    swFromNodes = {}
    swToNodes = {}
    for x in network_glmTree.keys():
        if 'from' in network_glmTree[x].keys():
            if network_glmTree[x]['from'] not in swFromNodes.keys():
                swFromNodes[network_glmTree[x]['from']] = 1
            else:
                swFromNodes[network_glmTree[x]['from']] += 1
            if network_glmTree[x]['to'] not in swToNodes.keys():
                swToNodes[network_glmTree[x]['to']] = 1
            else:
                swToNodes[network_glmTree[x]['to']] += 1
    deleteKeys = []
    for x in network_glmTree.keys():
        if network_glmTree[x].get('phase_A_state', '') == 'OPEN' or network_glmTree[x].get('phase_B_state', '') == 'OPEN' or network_glmTree[x].get('phase_C_state', '') == 'OPEN':
            if swToNodes[network_glmTree[x]['to']] > 1:
                pass
            elif swFromNodes.get(network_glmTree[x]['to'], 0) == 0:
                for phase in network_glmTree[x]['phases']:
                    if phase not in ['N', 'D']:
                        network_glmTree[x]['phase_{:s}_state'.format(phase)] = 'CLOSED'
            else:
                pass
    for key in deleteKeys:
        del network_glmTree[key]
        

    def _fixNominalVoltage(glm_dict, volt_dict):
        for x in glm_dict.keys():
            if 'from' in glm_dict[x].keys() and glm_dict[x]['from'] in volt_dict.keys() and glm_dict[x]['to'] not in volt_dict.keys():
                if glm_dict[x]['object'] == 'transformer':
                    # get secondary voltage from transformer configuration
                    if'SPCT' in glm_dict[x]['name']:
                        nv = '120.0'
                    else:
                        cnfg = glm_dict[x]['configuration']
                        for y in glm_dict.keys():
                            if 'name' in glm_dict[y].keys() and glm_dict[y]['name'] == cnfg:
                                nv = glm_dict[y]['secondary_voltage']
                    volt_dict[glm_dict[x]['to']] = nv
                elif glm_dict[x]['object'] == 'regulator':
                    volt_dict[glm_dict[x]['to']] = volt_dict[glm_dict[x]['from']]                 
                    cnfg = glm_dict[x]['configuration']
                    nv = volt_dict[glm_dict[x]['from']]  
                    for y in glm_dict.keys():
                        if glm_dict[y].get('name', '') == cnfg:
                            network_glmTree[y]['band_center'] = nv
                            network_glmTree[y]['band_width'] = str(float(network_glmTree[y]['band_width'])*float(network_glmTree[y]['band_center']))
                else:              
                    volt_dict[glm_dict[x]['to']] = volt_dict[glm_dict[x]['from']]
            elif 'parent' in glm_dict[x].keys() and glm_dict[x]['parent'] in volt_dict.keys() and glm_dict[x]['name'] not in volt_dict.keys():
                volt_dict[glm_dict[x]['name']] = volt_dict[glm_dict[x]['parent']]
                
    parent_voltage = {}
    current_parents = len(parent_voltage)
    previous_parents = 0
    
    for obj in network_glmTree:
        if 'bustype' in network_glmTree[obj] and network_glmTree[obj]['bustype'] == 'SWING':
            parent_voltage[network_glmTree[obj]['name']] = network_glmTree[obj]['nominal_voltage']
            current_parents = len(parent_voltage)
            
    while current_parents > previous_parents:
        _fixNominalVoltage(network_glmTree, parent_voltage)
        previous_parents = current_parents
        current_parents = len(parent_voltage)
        
    for x in network_glmTree.keys():
        if network_glmTree[x].get('name', '') in parent_voltage.keys():
            network_glmTree[x]['nominal_voltage'] = parent_voltage[network_glmTree[x].get('name', '')]
    # Delete nominal_voltage from link objects
    del_nom_volt_list = ['overhead_line', 'underground_line', 'regulator', 'transformer', 'switch', 'fuse', 'ZIPload', 'diesel_dg']
    for x in network_glmTree:
        if 'object' in network_glmTree[x].keys() and network_glmTree[x]['object'] in del_nom_volt_list and 'nominal_voltage' in network_glmTree[x].keys():
            del network_glmTree[x]['nominal_voltage']
    
    # Delete neutrals from links with no neutrals
    for x in network_glmTree.keys():
        if 'object' in network_glmTree[x].keys() and network_glmTree[x]['object'] in ['underground_line', 'regulator', 'transformer', 'switch', 'fuse', 'capacitor']:
            network_glmTree[x]['phases'] = network_glmTree[x]['phases'].replace('N', '')
        elif 'object' in network_glmTree[x].keys() and network_glmTree[x]['object'] == 'overhead_line':
            if network_glmTree[x]['configuration'] not in ohl_neutral:
                network_glmTree[x]['phases'] = network_glmTree[x]['phases'].replace('N', '')
        if 'object' in network_glmTree[x].keys() and network_glmTree[x]['object'] in ['node', 'meter']:
            try:
                network_glmTree[x]['phases'] = network_glmTree[x]['phases'].replace('S', '')
                if 'N' not in network_glmTree[x]['phases']:
                    network_glmTree[x]['phases'] = network_glmTree[x]['phases'] + 'N'
            except:
                pass
    for key in network_glmTree.keys():
        if 'object' in network_glmTree[key].keys() and network_glmTree[key]['object'] in ['overhead_line','underground_line','switch','fuse']:
            if network_glmTree[key]['from'] in connectedNodes and network_glmTree[key]['to'] in connectedNodes:
                pass
            else:
                commentedObj = {}
                for k, v in network_glmTree[key].items():
                    commentedObj['// ' + k] = v
                network_glmTree[key] = commentedObj

    equipment_glmTree = {}    # Dictionary that will hold the feeder model for conversion to .glm format
    load_glmTree = {}    # Dictionary that will hold the feeder model for conversion to .glm format
    caps_glmTree = {}    # Dictionary that will hold the feeder model for conversion to .glm format
    for i, ckey in enumerate(caps):
        caps_glmTree[i] = caps[ckey]
    key = 0
    for objDict in equipmentList:
        if len(objDict) > 0:
            for obj in objDict.keys():
                equipment_glmTree[key] = copy.deepcopy(objDict[obj])
                key = len(equipment_glmTree)
    for i, lkey in enumerate(loads):
        load_glmTree[i] = loads[lkey]
        load_glmTree[i]['phases'] = load_glmTree[i]['phases'].replace('N', '')
    _fixdupname(load_glmTree)
    ohls2_glmTree = {}
    for i, lkey in enumerate(ohls2):
        ohls2_glmTree[i] = ohls2[lkey]
    _fixdupname(ohls2_glmTree)
    for key in ohls2_glmTree.keys():
        if 'object' in ohls2_glmTree[key].keys() and ohls2_glmTree[key]['object'] == 'overhead_line':
            if ohls2_glmTree[key]['from'] in connectedNodes and ohls2_glmTree[key]['to'] in connectedNodes:
                pass
            else:
                commentedObj = {}
                for k, v in ohls2_glmTree[key].items():
                    commentedObj['// ' + k] = v
                ohls2_glmTree[key] = commentedObj
    return header_glmTree, network_glmTree, equipment_glmTree, caps_glmTree, load_glmTree, ohls2_glmTree, x_scale, y_scale


def _fixdupname(tree):
    for x in tree.keys():
        if 'object' in tree[x].keys() and tree[x]['object'] in ['node', 'meter', 'triplex_meter',
                                                                                      'triplex_node']:
            tree[x]['name'] = 'n' + tree[x]['name']
        if 'object' in tree[x].keys() and tree[x]['object'] == 'load':
            tree[x]['name'] = 'load_' + tree[x]['name']
        if 'from' in tree[x].keys():
            tree[x]['from'] = 'n' + tree[x]['from']
            tree[x]['to'] = 'n' + tree[x]['to']
        if 'parent' in tree[x].keys():
            tree[x]['parent'] = 'n' + tree[x]['parent']


def _readUndergroundCableGLMFile(ugConfig):
    if os.path.isfile(ugConfig):
        try:
            dictSource = open(ugConfig, 'r')
        except IOError:
            warnings.warn('An error occured trying to read the ' + ugConfig + ' file.')
        else:
            lines = dictSource.readlines()
            uglcfgDict = {}
            key = ''
            for line in lines:
                line = line.strip()
                if line[:2] == '//':
                    pass
                else:
                    words = line.split()
                    if len(words) >= 2:
                        if words[0] == 'object':
                            newEntry = dict()
                        elif words[0] == 'name':
                            key = words[1].strip(';\"\'')
                            # newEntry['name'] = key
                            uglcfgDict[key] = newEntry
                        else:
                            newEntry[words[0]] = line.lstrip(words[0]).strip().split(';')[0]
            return uglcfgDict


def _sortedWrite(inTree: object) -> object:
    ''' Write out a GLM from a tree, and order all tree objects by their key.
    Sometimes Gridlab breaks if you rearrange a GLM.
    '''
    sortedKeys = sorted(inTree.keys(), key=int)
    output = ''
    try:
        for key in sortedKeys:
            # print inTree[key]
            output += _dictToString(inTree[key]) + '\n'
    except ValueError:
        raise Exception
    return output


def _dictToString(inDict):
    ''' Helper function: given a single dict representing a GLM object, concatenate it into a string. '''
    # Handle the different types of dictionaries that are leafs of the tree root:
    if 'omftype' in inDict:
        return inDict['omftype'] + ' ' + inDict['argument'] + ';'
    elif 'module' in inDict:
        return 'module ' + inDict['module'] + ' {\n' + _gatherKeyValues(inDict, 'module') + '};\n'
    elif 'clock' in inDict:
        ## This object has known property order issues writing it out explicitly
        clock_string = 'clock {\n'
        timezone = inDict.get('timezone')
        if timezone is not None:
            clock_string += '\ttimezone ' + timezone + ';\n'
        else:
            warnings.warn('clock object did not have a timezone property', RuntimeWarning)
        clock_string += '\tstarttime ' + inDict['starttime'] + ';\n' + '\tstoptime ' + inDict['stoptime'] + ';\n};\n'
        return clock_string
    elif 'object' in inDict and inDict['object'] == 'schedule':
        return 'schedule ' + inDict['name'] + ' {\n' + inDict['cron'] + '\n};\n'
    elif 'object' in inDict:
        return 'object ' + inDict['object'] + ' {\n' + _gatherKeyValues(inDict, 'object') + '};\n'
    elif 'omfEmbeddedConfigObject' in inDict:
        return inDict['omfEmbeddedConfigObject'] + ' {\n' + _gatherKeyValues(inDict, 'omfEmbeddedConfigObject') + '};\n'
    elif '#include' in inDict:
        return '#include ' + '"' + inDict['#include'] + '"' + '\n'
    elif '#define' in inDict:
        return '#define ' + inDict['#define'] + '\n'
    elif '#set' in inDict:
        return '#set ' + inDict['#set'] + '\n'
    #Following was made to help with gridballast gridlabd functionality, so that frequency player doesn't need to be reopened
    elif 'class' in inDict and inDict['class'] =='player':
        return 'class' + ' ' + inDict['class'] + ' {\n' + '     ' + 'double' + ' ' + inDict['double'] + ';' + '\n};\n'
    elif '// object' in inDict:
        return '// object ' + inDict['// object'] + ' { //Disc\n' + _gatherKeyValues(inDict, '// object') + '// };\n'
    # elif 'collector' in inDict and 'group' in inDict and inDict['group'] =='class=ZIPload':
    # 	return 'object' + ' ' + inDict['object'] + ' {\n' + '	' + 'name' + ' ' + 'collector_ZIPloads' + ';'+'\n' +'group' + ' ' + inDict['group']+';'+'\n'+'property' +' '+inDict['property']+';'+'\n'+'interval'+' '+inDict['interval']+';'+'\n'+'file'+' '+inDict['file']+'\n};\n'


def _gatherKeyValues(inDict, keyToAvoid):
    ''' Helper function: put key/value pairs for objects into the format Gridlab needs. '''
    otherKeyValues = ''
    for key in inDict:
        if type(inDict[key]) is dict:
            # WARNING: RECURSION HERE
            otherKeyValues += _dictToString(inDict[key])
        elif key != keyToAvoid:
            if key == 'comment':
                otherKeyValues += (inDict[key] + '\n')
            elif key == 'name' or key == 'parent':
                if len(inDict[key]) <= 62:
                    otherKeyValues += ('\t' + key + ' ' + str(inDict[key]) + ';\n')
                else:
                    warnings.warn("{:s} argument is longer that 64 characters. Truncating.".format(key), RuntimeWarning)
                    otherKeyValues += ('\t' + key + ' ' + str(inDict[key])[0:62] + '; // truncated from {:s}\n'.format(inDict[key]))
            else:
                otherKeyValues += ('\t' + key + ' ' + str(inDict[key]) + ';\n')
    return otherKeyValues


def _tests(database, feederID, keepFiles=True, primaryLoadOnly=True, ugConfig=''):
    import os, json, traceback, shutil
    exceptionCount = 0       
    try:
        header, networkDict1, equipment, capsDict, loads, overheadlineType2, x, y = convertCymeModel(database, database, type=1, feeder_id=feederID,
                                           primaryLoadOnly=primaryLoadOnly, ugConfig=ugConfig)
        headerString = _sortedWrite(header)
        networkString = _sortedWrite(networkDict1)
        capstring = _sortedWrite(capsDict)
        equipmentStr = _sortedWrite(equipment)
        loadStr = _sortedWrite(loads)
        if primaryLoadOnly:
            headerfile = open("../output/" + feederID + "_header.glm", 'w')
            capfile = open("../output/" + feederID + "_caps.glm", 'w')
            equipmentfile = open("../output/" + 'CED_Equipment_' + feederID + ".glm", 'w')
            networkfile = open("../output/" + 'CED_Network_' + feederID + ".glm", 'w')
            loadfile = open("../output/" + 'CED_Loads_' + feederID + ".glm", 'w')
        else:
            gfile = open("../output/" + feederID + "_All_Triplex.glm", 'w')
        headerfile.write(headerString)
        headerfile.close()
        capfile.write(capstring)
        capfile.close()
        networkfile.write(networkString)
        networkfile.close()
        equipmentfile.write(equipmentStr)
        equipmentfile.close()
        loadfile.write(loadStr)
        loadfile.close()
        print('WROTE GLM FOR')
        outPrefix = '../output/' + feederID + '/'
    except Exception as ex:
        print('FAILED CONVERTING' + str(ex))
        exceptionCount += 1
        traceback.print_exc()
    if not keepFiles:
        shutil.rmtree(outPrefix)
    return exceptionCount


def _tests2(database, feederID1, feederID2, keepFiles=True, primaryLoadOnly=True, ugConfig='', deltaConnections='', connectedNodes=''):
    import os, json, traceback, shutil
    exceptionCount = 0
    deltaLoads = None
    if os.path.isfile(deltaConnections):
        with open(deltaConnections) as f:
            try:
                deltaLoads = json.load(f)
            except Exception as ex:
                print(str(ex))
    if os.path.isfile(connectedNodes):
        with open(connectedNodes) as f:
            try:
                # lines = f.readlines()
                disconnectedN = [line.rstrip().replace('.', '_') for line in f]
            except Exception as ex:
                print(str(ex))
    try:
        equipmentDict = {}
        olcDict = {}
        headerDict1, networkDict1, equipmentDict1, capsDict1, loadDict1, overheadlineType21, x1, y1 = convertCymeModel(database, database, type=1, feeder_id=feederID1,
                                                                                                                       primaryLoadOnly=primaryLoadOnly, ugConfig=ugConfig, equipments=equipmentDict, overheadlineConfig=olcDict, deltaConnections=deltaLoads, connectedNodes=disconnectedN)
        headerString1 = _sortedWrite(headerDict1)
        networkString1 = _sortedWrite(networkDict1)
        capstring1 = _sortedWrite(capsDict1)
        equipmentStr1 = _sortedWrite(equipmentDict1)
        loadStr1 = _sortedWrite(loadDict1)
        ohls2Str1 = _sortedWrite(overheadlineType21)
        if primaryLoadOnly:
            headerfile1 = open("../output/" + feederID1 + "_header.glm", 'w')
            capfile1 = open("../output/" + feederID1 + "_caps.glm", 'w')
            equipmentfile1 = open("../output/" + 'CED_Equipment_' + feederID1 + ".glm", 'w')
            networkfile1 = open("../output/" + 'CED_Network_' + feederID1 + ".glm", 'w')
            loadfile1 = open("../output/" + 'CED_Loads_' + feederID1 + ".glm", 'w')
            ohlt2file1 = open("../output/" + 'CED_Type2OverHeadLine_' + feederID1 + ".glm", 'w')
        else:
            gfile = open("../output/" + feederID1 + "_All_Triplex.glm", 'w')
        headerfile1.write(headerString1)
        headerfile1.close()
        capfile1.write(capstring1)
        capfile1.close()
        networkfile1.write(networkString1)
        networkfile1.close()
        equipmentfile1.write(equipmentStr1)
        equipmentfile1.close()
        loadfile1.write(loadStr1)
        loadfile1.close()
        ohlt2file1.write(ohls2Str1)
        ohlt2file1.close()
        if feederID2:
            headerDict2, networkDict2, equipmentDict2, capsDict2, loadDict2, overheadlineType22, x2, y2 = convertCymeModel(database, database, type=1, feeder_id=feederID2,
                                                                                                                           primaryLoadOnly=primaryLoadOnly, ugConfig=ugConfig, equipments=equipmentDict, overheadlineConfig=olcDict, deltaConnections=deltaLoads, connectedNodes=disconnectedN)

            headerString2 = _sortedWrite(headerDict2)
            networkString2 = _sortedWrite(networkDict2)
            capstring2 = _sortedWrite(capsDict2)
            equipmentStr2 = _sortedWrite(equipmentDict2)
            loadStr2 = _sortedWrite(loadDict2)
            ohls2Str2 = _sortedWrite(overheadlineType22)
            combinedEquipments = {}
            equipmentCount = 0
            for k, v in equipmentDict.items():
                for ekey, ev in v.items():
                    combinedEquipments[equipmentCount] = ev
                    equipmentCount += 1
            combinedEquipmentsStr = _sortedWrite(combinedEquipments)
            if primaryLoadOnly:
                headerfile2 = open("../output/" + feederID2 + "_header.glm", 'w')
                capfile2 = open("../output/" + feederID2 + "_caps.glm", 'w')
                equipmentfile2 = open("../output/" + 'CED_Equipment_' + feederID2 + ".glm", 'w')
                networkfile2 = open("../output/" + 'CED_Network_' + feederID2 + ".glm", 'w')
                loadfile2 = open("../output/" + 'CED_Loads_' + feederID2 + ".glm", 'w')
                ohlt2file2 = open("../output/" + 'CED_Type2OverHeadLine_' + feederID2 + ".glm", 'w')
                combinedEquipmentsfile = open("../output/" + 'CED_combinedEquipments.glm', 'w')
            else:
                gfile = open("../output/" + feederID2 + "_All_Triplex.glm", 'w')

            headerfile2.write(headerString2)
            headerfile2.close()
            capfile2.write(capstring2)
            capfile2.close()
            networkfile2.write(networkString2)
            networkfile2.close()
            equipmentfile2.write(equipmentStr2)
            equipmentfile2.close()
            loadfile2.write(loadStr2)
            loadfile2.close()
            ohlt2file2.write(ohls2Str2)
            ohlt2file2.close()
            combinedEquipmentsfile.write(combinedEquipmentsStr)
            combinedEquipmentsfile.close()
        print('WROTE GLM FOR')
    except Exception as ex:
        print('FAILED CONVERTING' + str(ex))
        exceptionCount += 1
        traceback.print_exc()
    return exceptionCount


if __name__ == '__main__':
    # if len(sys.argv) == 6:
    #     database = sys.argv[1]
    #     ugConfig = sys.argv[2]
    #     feedId1 = sys.argv[3]
    #     feedId2 = sys.argv[4]
    #     deltaConnections = sys.argv[5]
    #     _tests2(database, feedId1, feedId2, primaryLoadOnly=True, ugConfig=ugConfig, deltaConnections=deltaConnections)
    # elif len(sys.argv) == 5:
    #     database = sys.argv[1]
    #     ugConfig = sys.argv[2]
    #     feedId1 = sys.argv[3]
    #     if os.path.isfile(sys.argv[4]):
    #         deltaConnections = sys.argv[4]
    #         _tests2(database, feedId1, primaryLoadOnly=True, ugConfig=ugConfig, deltaConnections=deltaConnections)
    #     else:
    #         feedId2 = sys.argv[4]
    #         _tests2(database, feedId1, feedId2, primaryLoadOnly=True, ugConfig=ugConfig)
    if len(sys.argv) == 4:
        database = sys.argv[1]
        ugConfig = sys.argv[2]
        feedId = sys.argv[3]
        _tests(database, feedId, primaryLoadOnly=True, ugConfig=ugConfig)
    elif len(sys.argv) > 4 and len(sys.argv) < 8:
        database = sys.argv[1]
        ugConfig = sys.argv[2]
        feedId1 = sys.argv[3]
        feedId2 = None
        deltaConnections = None
        connectedNodes = None
        for i in range(4, len(sys.argv)):
            arg = sys.argv[i]
            if os.path.isfile(arg):
                ext = os.path.splitext(arg)[-1].lower()
                if ext == '.json':
                    deltaConnections = arg
                else:
                    connectedNodes = arg
            else:
                feedId2 = arg
        _tests2(database, feedId1, feedId2, primaryLoadOnly=True, ugConfig=ugConfig, deltaConnections=deltaConnections, connectedNodes=connectedNodes)
    else:
        warnings.warn("Usage: python cymeToGridlab_comEd4.py databaseFullPath undergroundConfigGLMFilePath feederID1 [feederID2] [deltaConnectionLoad] [connectedNodes]")
        warnings.warn("The 2nd feeder ID is optional, when provided, a combined equipment glm file will be generated.")
        warnings.warn("deltaConnectionLoad is an optional json file, when provided, load object that has a delta connection configuration will have different constant power properties from y connection configuration.")
        warnings.warn("connectedNodes is an optional file, when provided, it will be used to comment out objects that has either from or/and to nodes not provided by this file.")
        exit()