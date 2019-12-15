#!/bin/python3

import os
import ffmpeg
import requests
import json
import subprocess
import shutil
import re
import argparse
from pathlib import Path
import sys
import logging
import threading
from datetime import datetime, timedelta
import pytz
import plexapi
import concurrent.futures
import concurrent.futures.thread
import time



global SONARR_URL
SONARR_URL = "http://[SONARR URL/IP ADDRESS ]:8989/api/" #sonarr
global SONARR_APIKEY_PARAM
SONARR_APIKEY_PARAM = "?apikey=[SONARR API KEY]"
global RADARR_URL
global RADARR_APIKEY_PARAM
RADARR_URL = 'http://[RADARR URL/IP ]:7878/api/'
RADARR_APIKEY_PARAM = "?apikey=[RADARR API KEY HERE]"
global SeriesCache
SeriesCache = None
global RadarrCache
RadarrCache = None
global lastCacheRefreshTime
lastCacheRefreshTime = datetime.utcnow()
global P_Counter
P_Counter = 0
global P_Limit
P_Limit = 0
PLEX_URL = 'http://[PLEX URL/IP HERE]:32400'
PLEX_TOKEN = '[PLEX TOKEN HERE]'

def create_arg_parser():
    """"Creates and returns the ArgumentParser object."""


    parser = argparse.ArgumentParser(description='Description of your app.')
    parser.add_argument('--thread', '-t',
                        help="run each worker with 1 thread to allow other processes to work in parallel \
                          will run with 1 thread per flag present",

                        action='count')
    parser.add_argument('--daemon', '-d',
                        help='run as ongoing process, consider using with -O and/or -p',
                        action='store_true')
    parser.add_argument('--plex', '-p',
                        help="check and wait for there to be 0 plex clients before starting a transcode",

                        action='store_true')
    parser.add_argument('--worker', '-w',
                        help='the number of duplicate worker processes spawned, starts at one, each -w flag increments the count by 1',
                        default=1,
                        action='count')
    parser.add_argument('--limit',
                        '-l',
                        help='limit this to processing X items',
                        type=int,
                        default=0)
    parser.add_argument('--verbose',
                        '-v',
                        help="increase verbosity",
                        action='store_true')
    parser.add_argument('--offpeak',
                        '-O',
                        help="start worker threads that will only run during off peak hours",
                        action='store_true')
    parser.add_argument('--ignore_movies', '-m',
                        help='skip fetching movie paths for transcoding',
                        action='store_true')
    # parser.add_argument('--outputDirectory',
    # help='Path to the output that contains the resumes.')

    return parser

def GetRequest(apiType, queryParam = None):
    global SeriesCache
    global SONARR_APIKEY_PARAM
    global SONARR_URL
    queryString = ""
    if queryParam is not None:
        for q in queryParam:
            queryString += "&{}={}".format(q, queryParam[q])

    r = requests.get(SONARR_URL + apiType + SONARR_APIKEY_PARAM + queryString)
   
    jds = json.loads(r.content)
    SeriesCache = jds
    return jds


def GetRadarrRequest(apiType, queryParam = None):
    global RadarrCache
    global RADARR_URL
    global RADARR_APIKEY_PARAM
    queryString = ""
    if queryParam is not None:
        for q in queryParam:
            queryString += "&{}={}".format(q, queryParam[q])

    r = requests.get(RADARR_URL + apiType + RADARR_APIKEY_PARAM + queryString)

    jds = json.loads(r.content)
    RadarrCache = jds
    return jds



def GetRadarrMoviePaths():
    GetRadarrRequest('movie')
    Pathlist = list()
    for movie in RadarrCache:
        if movie['hasFile'] == True:
            movDir = movie['path']
            filePath = movDir +'/'+ movie['movieFile']['relativePath']
            Pathlist.append(filePath)
    return Pathlist

def NotifySonarrOfSeriesUpdate(seriesId: int = None):
    body: dict
    if seriesId is not None:
        body = {"name":"RefreshSeries", 'seriesId': seriesId}
    else:
        body = {"name":"RefreshSeries"}

    jsonbody = json.dumps(body)
    print("commanding sonarr to rescan")
    r = requests.post(SONARR_URL+"command"+SONARR_APIKEY_PARAM, jsonbody)
    print("response: {}".format(r.text))


def IsPlexBusy():
    from plexapi.server import PlexServer
    plex = PlexServer(PLEX_URL, PLEX_TOKEN)
    plexSessions = plex.sessions()
    if len(plexSessions) > 0:
        return True
    else:
        return False


def GetSeriesTitles(jsonInput):
    titleList = {'key':'title'}
    for thing in jsonInput:
        
        seriesTitle =  thing["title"]
        seriesId = thing["id"]
        newItem = {seriesTitle: seriesId}
        titleList.update(newItem)
    return titleList


def GetSeriesEpisodeList(seriesId):
    qp = {'seriesId': '{}'.format(seriesId)}    
    req = GetRequest("episode", qp )
    return req
            

def ProbeVideoFile(filePath):
    if os.path.exists(filePath) == False:
        if parsed_args.verbose == True:
            logging.debug(f'{filePath} does not exist')
        return 0
    try:
        fileMeta = ffmpeg.probe(filePath)
    except Exception as ex:
        logging.error(ex)
        logging.error(ffmpeg.Error)
        return 1
    else:
        return fileMeta


def GetSeriesFilePaths(seriesId):
    epList = GetSeriesEpisodeList(seriesId)
    pathList = list()
    for e in epList:
        if e['hasFile'] == True:
            epFile = e['episodeFile']['path']
            pathList.append(epFile)
    return pathList

def GetMasterFilePathList():
    global P_Counter
    global P_Limit
    global SeriesCache
    global RadarrCache
    filePaths = list()
    
    if parsed_args.ignore_movies != True:
        moviePaths = GetRadarrMoviePaths()
        if moviePaths is not None and len(moviePaths) > 0:
            filePaths.extend(moviePaths)
    for series in SeriesCache:
        if P_Limit !=0:
            if P_Counter >= P_Limit:
                if parsed_args.verbose == True:
                    logging.info("PCounter is >= P_Limit, skipping")
                    logging.debug("P_Count is {};; P_Limit is {}".format(P_Counter, P_Limit))
                return '-'
        i = series['id']
        seriesFilePaths = GetSeriesFilePaths(i)
        if seriesFilePaths is None or len(seriesFilePaths) < 1:
            pass
        else:
            filePaths.extend(seriesFilePaths)
    return filePaths







def ProcessFile(filePath):
    #double check the file
    global P_Counter
    global P_Limit

    PROCESS_THIS = False

    #limit check
    if P_Limit !=0:
        # if parsed_args.verbose == True:
        #     logging.debug("P_Count is {};; P_Limit is {}".format(P_Counter, P_Limit))
        if P_Counter >= P_Limit:
            if parsed_args.verbose == True:
                print("limit exceeded, skipping")
            return '-'

    meta = ProbeVideoFile(filePath)
    if meta == 1 or meta == 0:
        return None
    #if container is not mp4 then we need to convert anyway
    if  re.search(".mp4$", filePath) == None:
        PROCESS_THIS = True

    if PROCESS_THIS == False and meta is not None:
        streams = meta['streams']
        for s in streams:
            if s['codec_type'] == 'audio':
                if s['codec_name'] != 'aac':
                    PROCESS_THIS = True
                    break
            if s['codec_type'] == 'video':
                if s['codec_name'] != 'h264':
                    PROCESS_THIS = True
                    break
                    
    if PROCESS_THIS == True:
        logging.info("{} is candidate for processing (P_Count is {}, P_Limit is {})".format(filePath, P_Counter, P_Limit))
        returnCode = convertVideoFile(filePath)
        if returnCode == 0:
            return 0
    else:
        # if not a candidate, return none
        return None

def ffmpegArgumentAssembly(sanitizedFileName: str, jsonFileMeta, containerType: str):
    argList = list()
    argList.append("-y")
    argList.append("-map 0")


    vArgs = ffmpegVideoConversionArgument(jsonFileMeta)
    if vArgs is not None:
        argList.extend(vArgs)
    elif vArgs is None:
        argList.append('-vcodec copy')
    aArgs = ffmpegAudioConversionArgument(jsonFileMeta)
    if aArgs is not None:
        argList.extend(aArgs)
    elif aArgs is None:
        argList.append('-acodec copy')
    sArgs = ffmpegSubtitleConversionArgument(jsonFileMeta, containerType)
    if sArgs is not None:
        argList.extend(sArgs)
    if parsed_args.thread is not None:
        argList.append(f'-threads {parsed_args.thread}')
    # force file overwrite
    #argList.append("-map_metadata 0")
    #add input file
    if parsed_args.verbose == True:
        logging.debug(f"vArgs is {vArgs}; aArgs is {aArgs}; file ends with .mp4 bools is {sanitizedFileName.endswith('.mp4')}")
    if vArgs is None and aArgs is None and re.search(".mp4$|.mkv$", sanitizedFileName) is not None:
        
        # if all three conditions are met, then we don't need to convert
        return 2
    separator = " "
    if re.search(".mp4$|.mkv$", sanitizedFileName) is not None:
    # assemble ffmpeg command with argument list and output file name
        joinedArgString = f"ffmpeg -i \'{sanitizedFileName}\' {separator.join(argList)} \'{sanitizedFileName + '.converting' + containerType }\' "
    else:
        joinedArgString = f"ffmpeg -i \'{sanitizedFileName}\' {separator.join(argList)} \'{sanitizedFileName + '.converting.mkv' }\' "

    if parsed_args.verbose == True:
        logging.debug(joinedArgString)
    return joinedArgString



def ffmpegVideoConversionArgument(jsonFileMeta):
    # define the rules for video conversion here
    try:
        videoArgs = set()
        streams = jsonFileMeta['streams']
        
        for s in streams:
            if s['codec_type'] == 'video':
                # currently only care about it being h264
                # TODO: add resolution and fps tweaks
                if s['codec_name'] != 'h264':
                    videoArgs.add('-vcodec h264')
                fps: float
                fpsFrac = s['r_frame_rate']
                if len(fpsFrac) == 0:
                    fps = fpsFrac
                else:
                    splitFrac = fpsFrac.split('/')
                    fps = int(splitFrac[0])/int(splitFrac[1])
                if fps >= 30:
                    videoArgs.add('-framerate 24')
                try:
                    if s['tags'] is not None:
                            if s['tags']['mimetype'] is not None:
                                if s['tags']['mimetype'] == 'image/jpeg':
                                    videoArgs.add(f"-map -0:{s['index']}")
                except Exception as ex:
                    pass
            else:
                pass
        if len(videoArgs) == 0:
                    return None
        else:
            videoArgs.add('-vsync 2')
            videoArgs.add('-r 30')
            videoArgs.add('-max_muxing_queue_size 1000')
            videoArgs.add('-analyzeduration 25000')
            videoArgs.add('-probesize 50000000')
            return videoArgs
    except Exception as ex:
        logging.error(ex)


def ffmpegAudioConversionArgument(jsonFileMeta):
    # define the rules for audio conversion here 
    try:
        audioArgs = set()
        streams = jsonFileMeta['streams']
        for s in streams:
            if s['codec_type'] == 'audio':
                # we want everything to be in 2 channel aac
                if s['codec_name'] != 'aac':
                    audioArgs.add("-acodec aac")
                if s['channels'] != 2:
                    audioArgs.add("-ac 2")
                if len(audioArgs) == 0:
                    return None
                return audioArgs
            else:
                pass
    except Exception as ex:
        logging.error(ex)



def ffmpegSubtitleConversionArgument(jsonFileMeta, containerType: str):
    # define the rules for audio conversion here 
    if re.search(".mkv$", containerType) is None:
        try:
            subtArgs = set()
            streams = jsonFileMeta['streams']
            for s in streams:
                if s['codec_type'] == 'subtitle':
                    if str(s['codec_name']).casefold() == "dvd_subtitle".casefold():
                        subtArgs.add(f"-map -0:{s['index']}")
                    else:
                        pass
                    #remove subtitle stream mappings
                        

                    
                    # if s['channels'] != 2:
                    #     subtArgs.append("-ac 2")

                    #for now just copy subtitles
                    
                    # if len(subtArgs) == 0:
                        #tell it not to map subtitles, mp4 doesn't support them as streams anyway
                    #  subtArgs.append("-scodec copy")
                else:
                    pass
            return subtArgs
        except Exception as ex:
            print(ex)
            logging.error(ex)
    else:
        return set()



def SaniString(sInput: str):
    # splitString = sInput.split()
    # outputString: str
    # outputString = ""
    # for e in splitString:
    #     outputString += e + "\ "
    # outputString = outputString.strip()
    # outputString = outputString.strip("\\")
    # outputString = outputString.replace("\'", '\\' + "'")
    # outputString = outputString.replace('\&', '\&\&')
    # ampsplitstring = outputString.split('&')
    # newOutputstring = ""
    # for a in ampsplitstring:
    #     newOutputstring += a + '\\&'
    # newOutputstring = newOutputstring.strip('\\&')
    sInput = sInput.replace("\'", '\''+"\\'"+"\'")
    return sInput




def convertVideoFile(file):
    global P_Counter

    sanitizedString = SaniString(file)
    tempFileName = sanitizedString + ".converting.mp4"
    if parsed_args.verbose:
        logging.debug('temp file name is {} ;; sanitized file name is {}'.format(tempFileName, sanitizedString))
    try:
        logging.info("conversion of {} beginning".format(file))
       # print("ffmpeg -y -i " + sanitizedString+ " -vcodec copy -ac 2 -acodec aac " + tempFileName)
        convArgs: str
        # if  re.search(".mkv$", file):
        #     convArgs = "ffmpeg -y -i " + sanitizedString+ " -ac 2 -acodec aac " + tempFileName
        # elif re.search(".avi$", file):
        #     convArgs = "ffmpeg -y -i " + sanitizedString+ " -ac 2 -acodec aac " + tempFileName
        # else:
        #     convArgs = "ffmpeg -y -i " + sanitizedString+ " -vcodec copy -ac 2 -acodec aac " + tempFileName
        jsonFileMeta = ProbeVideoFile(file)
        if jsonFileMeta == 1 or jsonFileMeta == 0:
            return 1
        containerType: str
        if re.search(".mp4$", sanitizedString) is not None:
            containerType = ".mp4"
        elif re.search(".mkv$", sanitizedString) is not None:
            containerType = ".mkv"
        else:
            containerType = ".mkv"
        convArgs = ffmpegArgumentAssembly(sanitizedString, jsonFileMeta, containerType)
        if convArgs == 2:
            if parsed_args.verbose == True:
                logging.debug(f'{file} already meets criteria, skipping')
            return 0

        if parsed_args.verbose == True:
            logging.debug(f'args: \n {convArgs} \n')
        #newArgs = convArgs.replace('&', '\&')
        newArgs = convArgs
        if parsed_args.verbose == True:
            logging.debug(f'args:: \\n {newArgs} \\n')
        convProcess: p = subprocess.Popen(newArgs,
                    stdout=subprocess.PIPE,
                                                shell=True)
        (output, err) = convProcess.communicate()
        print(output)
        print(err)
        p_status = convProcess.wait()
        if convProcess.returncode == 0:
            logging.info("success converting {}".format(file))
            P_Counter = P_Counter + 1
            
        elif convProcess.returncode == 1:
            logging.error("error converting")
            logging.error(err)
            P_Counter = P_Counter + 1
            raise ChildProcessError
        else:
            print("return code is {}".format(convProcess.returncode))
            P_Counter = P_Counter + 1
            raise EnvironmentError
       
    except Exception as ex:
        logging.error(ex)
        return 1
    else:
        try:
            # path is not literal, so we don't want the 'sanitized' version
            if containerType == '.mkv' or containerType == '.mp4':
                tempFileName_unsanitized = file + '.converting' + containerType
            else:
                tempFileName_unsanitized = file + '.converting.mkv'
            if  re.search(".mkv$", file):
                newFileName = file
                
                shutil.move(tempFileName_unsanitized, newFileName)
                if parsed_args.verbose == True:
                    logging.debug(f'moved {tempFileName_unsanitized} over {newFileName}')
            elif re.search(".avi$", file):
                newFileName = file.strip(".avi")
                newFileName += ".mp4"
                shutil.move(tempFileName_unsanitized, newFileName)
                if parsed_args.verbose == True:
                    logging.debug(f'moved {tempFileName_unsanitized} over {newFileName}')
            else:
                shutil.move(tempFileName_unsanitized, file)
                if parsed_args.verbose == True:
                    logging.debug(f'moved {tempFileName_unsanitized} over {file}')
                return 0
        except Exception as ex:
            logging.error(ex)
            return 1
        else:
            if file != newFileName:
                os.remove(file) 
                if parsed_args.verbose == True:
                    logging.debug(f'deleting original file: {file}')
                
            logging.debug("completed processing of {}".format(file))
            return 0



def FindEpisodeFileIdFromFilePath(filePath: str):
    for series in SeriesCache:
        sPath = series['path']
        if filePath.startswith(sPath):
            epList = GetSeriesEpisodeList(series['id'])
            for e in epList:
                if e['hasFile'] == True:
                    if e['episodeFile']['path'] == filePath:
                        return e['episodeFile']['id']



def ScanVideoFiles(jsonResponse):
    for series in jsonResponse:
            i = series['id']
            filePaths = GetSeriesFilePaths(i)
            if filePaths is None:
                return
            try:
                for f in filePaths:
                        outputString = "{}".format(f)
                        meta = ProbeVideoFile(f)
                        if meta == 1 or meta == 2:
                            pass
                        else:
                            streams = meta['streams']
                            for s in streams:
                                if s['codec_type'] == 'video':
                                    outputString += "|video={}".format(s['codec_name'])
                                if s['codec_type'] == 'audio':
                                    outputString += "|audio={}".format(s['codec_name'])
                            print(outputString)
            except Exception as ex:
                        logging.exception(ex)

def RefreshCache(durationSeconds: int):
    global lastCacheRefreshTime
    cacheLifetime: timedelta
    cacheLifetime = datetime.utcnow() - lastCacheRefreshTime
    if cacheLifetime > timedelta(0, durationSeconds, 0):
        GetRequest("series")
        GetRadarrRequest("movie")
        lastCacheRefreshTime = datetime.utcnow()
        return
    else:
        return

def worker(event):
    global P_Counter
    global P_Limit
    global lastCacheRefreshTime
    lastCacheRefreshTime = datetime.utcnow()
    GetRequest("series")
    GetRadarrRequest("movie")
    while not event.isSet():
        try:
            RefreshCache(3600)
            filePaths = GetMasterFilePathList()
            with concurrent.futures.thread.ThreadPoolExecutor(max_workers=parsed_args.worker) as executor:
                
                for f in filePaths:
                    if not event.isSet():

                        if parsed_args.verbose == True:
                            logging.debug("worker thread checking in")
                        
                        executor.submit(workerProcess,f)
                        
                        

                        if P_Limit != 0:
                            if P_Counter >= P_Limit:
                                event.set() 
                    else:
                        break
                if parsed_args.daemon is True:
                    event.set()              
        except Exception as ex:
            logging.error(ex)
        

def workerProcess(file: str):
    try:
    
        
        shouldRun = IsAllowedToRunDetermination()
        while shouldRun == False:
            if parsed_args.verbose == True:
                logging.debug("restrictions not met, sleeping for 300 seconds")
            print('run criteria not met, waiting')
            time.sleep(150)
            # after the pause, check again to see if run restrictions are met
            shouldRun = IsAllowedToRunDetermination()
            pass

        p = ProcessFile(file)
        if p is not None:
            if p == '-':
                pass
            elif p == 0:
                print(" RETURN 0 \n\n")
                return 0
            elif p== 1:
                print(" RETURN 1 \n\n")
                raise ChildProcessError
                return 1
            else:
                pass
    except Exception as ex:
        logging.error(ex)
        if parsed_args.verbose == True:
            print(f"error while processing {file}")



def IsAllowedToRunDetermination():
    if parsed_args.offpeak == True:
        timeFactor = IsAllowedToRun_Time()
        if timeFactor == False:
            print("waiting due to time restrictions")
            if parsed_args.verbose == True:
                logging.info("not running due to time restrictions")
            return False
        else:
            pass
    if parsed_args.plex == True:
        # dont run if IsPlexBusy is true
        plexFactor = IsPlexBusy()
        if plexFactor == True:
            print("skipping due to plex client activity")
            if parsed_args.verbose == True:
                logging.info("not running due to plex client activity")
            return False
        else:
            pass
    # if nothing caused the check to fail, then give the green light to start processing
    return True



def IsAllowedToRun_Time():
    if parsed_args.offpeak == True:
        tz_LA = pytz.timezone('America/Los_Angeles')
        nowTime = datetime.now(tz_LA)
        dow = nowTime.isoweekday()
        hr = nowTime.hour
        # if it's a weekday (mon-fri)
        if 5 >= dow >= 1:
            #define weekday time constraints here
            if 17 >= hr >= 9:
                #working hours
                return True
            elif 6 >= hr >= 0:
                #sleeping hours
                return True
            elif 24>= hr >= 22:
                return True
            else:
                return False
        # if it's the weekend: we expect video to be used during the day
        # only run during the deep night
        if 7 >= dow >= 6:
            if 8 >= hr >= 0:
                return True
            elif 24 >= hr >= 22:
                return True
            else:
                return False
        
        #fall through return false just in case; should never get hit
        return False
    else:
        # if we're not worried about time, then it's always allowed to run
        return True
    


if __name__ == "__main__":
    global startTime
    P_Counter = 0
    P_Limit = 0
    startTime = datetime.utcnow()

    arg_parser = create_arg_parser()
    parsed_args = arg_parser.parse_args(sys.argv[1:])
    logging.debug(parsed_args)
    
    if parsed_args.limit is not None:
        if parsed_args.limit != 0:
            P_Limit = parsed_args.limit
    try:
        logging.basicConfig(
            force=True,
            level=logging.DEBUG,
            format="%(asctime)s %(threadName)s %(lineno)d %(message)s",
            filename="sonarr_trans_log.log"
        )
        logging.critical("initializing")
        # except Exception as ex:
        #     logging.basicConfig(
        #         level=logging.debug,
        #         format="%(asctime)s %(threadName)s %(lineno)d %(message)s"

        #     )
    except Exception as ex:
        print(ex)
        logging.critical(ex)    
    if parsed_args.verbose == True:
        print("verbose mode on")
        logging.debug("Initilizing with P_Count: {};; P_Limit: {}".format(P_Counter, P_Limit))

    event = threading.Event()
    thread = threading.Thread(target=worker, args=(event,))
    #thread_two = threading.Thread(target=worker, args=(event,))
    thread.start()
    
    #thread_two.start()

    while not event.isSet():
        try:
            if parsed_args.verbose == True:
                print("Checking in from main thread")
            elapsedTime = datetime.utcnow() - startTime
            if parsed_args.verbose == True:
                print(f'elapsed time is {elapsedTime}')
            
            # stop after 1 day to prevent zombie processes
            if elapsedTime > timedelta(3):
                break
            event.wait(30)
        except KeyboardInterrupt:
            event.set()
            break


    # if parsed_args.notify == True:
    #     NotifySonarrOfSeriesUpdate()
    # else:
    #     main()
    # pass