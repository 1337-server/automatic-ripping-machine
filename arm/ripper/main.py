#!/usr/bin/env python3

import sys

sys.path.append("/opt/arm")

import argparse  # noqa: E402
import os  # noqa: E402
import logging  # noqa: E402
import time  # noqa: E402
import datetime  # noqa: E402
import re  # noqa: E402
import shutil  # noqa: E402
import pyudev  # noqa: E402
import getpass  # noqa E402
import psutil  # noqa E402
from pathlib import Path, PurePath  # noqa: E402
from arm.ripper import logger, utils, makemkv, handbrake, identify  # noqa: E402
from arm.config.config import cfg  # noqa: E402

from arm.ripper.getkeys import grabkeys  # noqa: E402
from arm.models.models import Job, Config  # noqa: E402
from arm.ui import app, db  # noqa E402


def entry():
    """ Entry to program, parses arguments"""
    parser = argparse.ArgumentParser(description='Process disc using ARM')
    parser.add_argument('-d', '--devpath', help='Devpath', required=True)

    return parser.parse_args()


def log_udev_params():
    """log all udev parameters"""

    logging.debug("**** Logging udev attributes ****")
    # logging.info("**** Start udev attributes ****")
    context = pyudev.Context()
    device = pyudev.Devices.from_device_file(context, '/dev/sr0')
    for key, value in device.items():
        logging.debug(key + ":" + value)
    logging.debug("**** End udev attributes ****")


def log_arm_params(job):
    """log all entry parameters"""

    # log arm parameters
    logging.info("**** Logging ARM variables ****")
    for key in ("devpath", "mountpoint", "title", "year", "video_type",
                "hasnicetitle", "label", "disctype"):
        logging.info(
            key + ": " + str(getattr(job, key)))
    logging.info("**** End of ARM variables ****")

    logging.info("**** Logging config parameters ****")
    for key in ("SKIP_TRANSCODE", "MAINFEATURE", "MINLENGTH", "MAXLENGTH",
                "VIDEOTYPE", "MANUAL_WAIT", "MANUAL_WAIT_TIME", "RIPMETHOD",
                "MKV_ARGS", "DELRAWFILES", "HB_PRESET_DVD", "HB_PRESET_BD",
                "HB_ARGS_DVD", "HB_ARGS_BD", "RAW_PATH", "TRANSCODE_PATH",
                "COMPLETED_PATH", "EXTRAS_SUB", "EMBY_REFRESH", "EMBY_SERVER",
                "EMBY_PORT", "NOTIFY_RIP", "NOTIFY_TRANSCODE",
                "MAX_CONCURRENT_TRANSCODES"):
        logging.info(key.lower() +
                     ": " +
                     str(cfg.get(key, '<not given>')))
    logging.info("**** End of config parameters ****")


def check_fstab():
    logging.info("Checking for fstab entry.")
    with open('/etc/fstab', 'r') as f:
        lines = f.readlines()
        for line in lines:
            # Now grabs the real uncommented fstab entry
            if re.search("^" + job.devpath, line):
                logging.info("fstab entry is: " + line.rstrip())
                return
    logging.error("No fstab entry found.  ARM will likely fail.")


def check_ip():
    """
        Check if user has set an ip in the config file
        if not gets the most likely ip
        arguments:
        none
        return: the ip of the host or 127.0.0.1
    """
    host = cfg['WEBSERVER_IP']
    if host == 'x.x.x.x':
        # autodetect host IP address
        from netifaces import interfaces, ifaddresses, AF_INET
        ip_list = []
        for interface in interfaces():
            inet_links = ifaddresses(interface).get(AF_INET, [])
            for link in inet_links:
                ip = link['addr']
                # print(str(ip))
                if ip != '127.0.0.1' and not (ip.startswith('172')):
                    ip_list.append(ip)
                    # print(str(ip))
        if len(ip_list) > 0:
            return ip_list[0]
        else:
            return '127.0.0.1'
    else:
        return host


def main(logfile, job):
    """main dvd processing function"""
    logging.info("Starting Disc identification")

    identify.identify(job, logfile)
    # Check db for entries matching the crc and successful
    have_dupes, crc_jobs = utils.job_dupe_check(job)

    # DVD disk entry
    if job.disctype in ["dvd", "bluray"]:
        # Send the notifications
        utils.notify(job, "ARM notification",
                     f"Found disc: {job.title}. Disc type is {job.disctype}. Main Feature is {cfg['MAINFEATURE']}"
                     f".  Edit entry here: http://" + str(check_ip()) + ":"
                     f"{cfg['WEBSERVER_PORT']}/jobdetail?job_id={job.job_id}")
    elif job.disctype == "music":
        utils.notify(job, "ARM notification", f"Found music CD: {job.label}. Ripping all tracks")
    elif job.disctype == "data":
        utils.notify(job, "ARM notification", "Found data disc.  Copying data.")
    else:
        utils.notify(job, "ARM Notification", "Could not identify disc.  Exiting.")
        sys.exit()

    # TODO: Update function that will look for the best match with most data
    #  If we have have waiting for user input enabled
    if cfg["MANUAL_WAIT"]:
        logging.info(f"Waiting {cfg['MANUAL_WAIT_TIME']} seconds for manual override.")
        job.status = "waiting"
        db.session.commit()
        sleep_time = 0
        while sleep_time < cfg["MANUAL_WAIT_TIME"]:
            time.sleep(5)
            sleep_time += 5
            db.session.refresh(job)
            db.session.refresh(config)
            if job.title_manual:
                break
        job.status = "active"
        db.session.commit()

    # If the user has set info manually update database and hasnicetitle
    if job.title_manual:
        logging.info("Manual override found.  Overriding auto identification values.")
        job.updated = True
        # We need to let arm know we have a nice title so it can use the MEDIA folder and not the ARM folder
        job.hasnicetitle = True
    else:
        logging.info("No manual override found.")

    log_arm_params(job)
    check_fstab()

    if cfg["HASHEDKEYS"]:
        logging.info("Getting MakeMKV hashed keys for UHD rips")
        grabkeys()

    # Entry point for dvd/bluray
    if job.disctype in ["dvd", "bluray"]:
        # get filesystem in order
        # If we have a nice title/confirmed name use the MEDIA_DIR and not the ARM unidentified folder
        # if job.hasnicetitle:
        if job.video_type == "movie":
            type_sub_folder = "movies"
        elif job.video_type == "series":
            type_sub_folder = "tv"
        else:
            type_sub_folder = "unidentified"

        if job.year != "0000" or job.year != "":
            hb_out_path = os.path.join(cfg["TRANSCODE_PATH"], str(type_sub_folder),
                                       str(job.title) + " (" + str(job.year) + ")")
        else:
            hb_out_path = os.path.join(cfg["TRANSCODE_PATH"], str(type_sub_folder), str(job.title))

        # The dvd directory already exists - Lets make a new one using random numbers
        if (utils.make_dir(hb_out_path)) is False:
            logging.info("Directory exist.")
            # Only begin ripping if we are allowed to make duplicates
            # Or the successful rip of the disc is not found in our database
            if cfg["ALLOW_DUPLICATES"] or not have_dupes:
                ts = round(time.time() * 100)
                hb_out_path = hb_out_path + "_" + str(ts)

                if (utils.make_dir(hb_out_path)) is False:
                    # We failed to make a random directory, most likely a permission issue
                    logging.exception(
                        "A fatal error has occurred and ARM is exiting.  "
                        "Couldn't create filesystem. Possible permission error")
                    utils.notify(job, "ARM notification", "ARM encountered a fatal error processing " + str(
                        job.title) + ".  Couldn't create filesystem. Possible permission error. ")
                    job.status = "fail"
                    db.session.commit()
                    sys.exit()
            else:
                # We arent allowed to rip dupes, notify and exit
                logging.info("Duplicate rips are disabled.")
                utils.notify(job, "ARM notification", "ARM Detected a duplicate disc. For " + str(
                    job.title) + ".  Duplicate rips are disabled. You can re-enable them from your config file. ")
                job.status = "fail"
                db.session.commit()
                sys.exit()

        logging.info("Processing files to: " + hb_out_path)

        # entry point for bluray
        # or
        # dvd with MAINFEATURE off and RIPMETHOD mkv
        hb_in_path = str(job.devpath)
        if job.disctype == "bluray" or (not cfg["MAINFEATURE"] and cfg["RIPMETHOD"] == "mkv"):
            # send to makemkv for ripping
            # run MakeMKV and get path to output
            job.status = "ripping"
            db.session.commit()
            try:
                mkvoutpath = makemkv.makemkv(logfile, job)
            except:  # noqa: E722
                raise

            if mkvoutpath is None:
                logging.error("MakeMKV did not complete successfully.  Exiting ARM!")
                job.status = "fail"
                db.session.commit()
                sys.exit()
            if cfg["NOTIFY_RIP"]:
                utils.notify(job, "ARM notification", str(job.title) + " rip complete.  Starting transcode. ")
            # point HB to the path MakeMKV ripped to
            hb_in_path = mkvoutpath

            # Entry point for not transcoding
            if cfg["SKIP_TRANSCODE"] and cfg["RIPMETHOD"] == "mkv":
                logging.info("SKIP_TRANSCODE is true.  Moving raw mkv files.")
                logging.info("NOTE: Identified main feature may not be actual main feature")
                files = os.listdir(mkvoutpath)
                final_directory = hb_out_path
                if job.video_type == "movie":
                    logging.debug("Videotype: " + job.video_type)
                    # if videotype is movie, then move biggest title to media_dir
                    # move the rest of the files to the extras folder

                    # find largest filesize
                    logging.debug("Finding largest file")
                    largest_file_name = ""
                    for f in files:
                        # initialize largest_file_name
                        if largest_file_name == "":
                            largest_file_name = f
                        temp_path_f = os.path.join(hb_in_path, f)
                        temp_path_largest = os.path.join(hb_in_path, largest_file_name)
                        # os.path.join(cfg['MEDIA_DIR'] + videotitle)
                        # if cur file size > largest_file size
                        if (os.stat(temp_path_f).st_size > os.stat(temp_path_largest).st_size):
                            largest_file_name = f
                    # largest_file should be largest file
                    logging.debug("Largest file is: " + largest_file_name)
                    temp_path = os.path.join(hb_in_path, largest_file_name)
                    if (os.stat(temp_path).st_size > 0):  # sanity check for filesize
                        for file in files:
                            # move main into media_dir
                            # move others into extras folder
                            if (file == largest_file_name):
                                # largest movie
                                # Encorporating Rajlaud's fix #349
                                utils.move_files(hb_in_path, file, job, True)
                            else:
                                # other extras
                                if not str(cfg["EXTRAS_SUB"]).lower() == "none":
                                    # Incorporating Rajlaud's fix #349
                                    utils.move_files(hb_in_path, file, job, False)
                                else:
                                    logging.info("Not moving extra: " + file)
                    # Change final path (used to set permissions)
                    final_directory = os.path.join(cfg["COMPLETED_PATH"], str(type_sub_folder),
                                                   str(job.title) + " (" + str(job.year) + ")")
                    # Clean up
                    # TODO: fix this so it doesnt remove everything
                    logging.debug("Attempting to remove extra folder in TRANSCODE_PATH: " + hb_out_path)
                    if hb_out_path != final_directory:
                        try:
                            shutil.rmtree(hb_out_path)
                            logging.debug("Removed sucessfully: " + hb_out_path)
                        except Exception:
                            logging.debug("Failed to remove: " + hb_out_path)
                else:
                    # if videotype is not movie, then move everything
                    # into 'Unidentified' folder
                    logging.debug("Videotype: " + job.video_type)

                    for f in files:
                        mkvoutfile = os.path.join(mkvoutpath, f)
                        logging.debug("Moving file: " + mkvoutfile + " to: " + mkvoutpath + f)
                        shutil.move(mkvoutfile, hb_out_path)
                # remove raw files, if specified in config
                if cfg["DELRAWFILES"]:
                    logging.info("Removing raw files")
                    shutil.rmtree(mkvoutpath)
                # set file to default permissions '777'
                if cfg["SET_MEDIA_PERMISSIONS"]:
                    perm_result = utils.set_permissions(job, final_directory)
                    logging.info("Permissions set successfully: " + str(perm_result))
                utils.notify(job, "ARM notification", str(job.title) + " processing complete. ")
                logging.info("ARM processing complete")
                # WARN  : might cause issues
                # We need to update our job before we quit
                # It should be safe to do this as we arent waiting for transcode
                job.status = "success"
                db.session.commit()
                # exit
                job.eject()
                sys.exit()
        job.path = hb_out_path
        job.status = "transcoding"
        db.session.commit()
        if job.disctype == "bluray" and cfg["RIPMETHOD"] == "mkv":
            handbrake.handbrake_mkv(hb_in_path, hb_out_path, logfile, job)
        elif job.disctype == "dvd" and (not cfg["MAINFEATURE"] and cfg["RIPMETHOD"] == "mkv"):
            handbrake.handbrake_mkv(hb_in_path, hb_out_path, logfile, job)
        elif job.video_type == "movie" and cfg["MAINFEATURE"] and job.hasnicetitle:
            handbrake.handbrake_mainfeature(hb_in_path, hb_out_path, logfile, job)
            job.eject()
        else:
            handbrake.handbrake_all(hb_in_path, hb_out_path, logfile, job)
            job.eject()

        # check if there is a new title and change all filenames
        # time.sleep(60)
        db.session.refresh(job)
        logging.debug("New Title is " + str(job.title_manual))
        if job.year != "0000" or job.year != "":
            final_directory = os.path.join(job.config.COMPLETED_PATH, str(type_sub_folder),
                                           str(job.title) + " (" + str(job.year) + ")")
        else:
            final_directory = os.path.join(job.config.COMPLETED_PATH, str(type_sub_folder), str(job.title))

        # move to media directory
        if job.video_type == "movie" and job.hasnicetitle:
            # tracks = job.tracks.all()
            tracks = job.tracks.filter_by(ripped=True)
            # tracks = job.tracks.filter(job.tracks.length > cfg['MINLENGTH'])
            for track in tracks:
                logging.info("Moving Movie " + str(track.filename) + " to " + str(p))
                utils.move_files(hb_out_path, track.filename, job, track.main_feature)
        # move to media directory
        elif job.video_type == "series" and job.hasnicetitle:
            # tracks = job.tracks.all()
            tracks = job.tracks.filter_by(ripped=True)
            for track in tracks:
                logging.info("Moving Series " + str(track.filename) + " to " + str(final_directory))
                utils.move_files(hb_out_path, track.filename, job, False)
        else:
            logging.info("job type is " + str(job.video_type) + "not movie or series, not moving.")
            utils.scan_emby(job)

        if cfg["SET_MEDIA_PERMISSIONS"]:
            perm_result = utils.set_permissions(job, final_directory)
            logging.info("Permissions set successfully: " + str(perm_result))

        # Clean up bluray backup
        # if job.disctype == "bluray" and cfg["DELRAWFILES"]:
        if cfg["DELRAWFILES"]:
            raw_list = [mkvoutpath, hb_out_path, hb_in_path]
            for raw_folder in raw_list:
                try:
                    logging.info(f"Removing raw path - {raw_folder}")
                    shutil.rmtree(raw_folder)
                except UnboundLocalError as e:
                    logging.debug(f"No raw files found to delete in {raw_folder}- {e}")
                except OSError as e:
                    logging.debug(f"No raw files found to delete in {raw_folder} - {e}")

        # report errors if any
        if job.errors:
            errlist = ', '.join(job.errors)
            if cfg["NOTIFY_TRANSCODE"]:
                utils.notify(job, "ARM notification",
                             str(job.title) + " processing completed with errors. Title(s) " + str(
                                 errlist) + " failed to complete. ")
            logging.info("Transcoding completed with errors.  Title(s) " + str(errlist) + " failed to complete. ")
        else:
            if cfg["NOTIFY_TRANSCODE"]:
                utils.notify(job, "ARM notification", str(job.title) + " processing complete. ")
            logging.info("ARM processing complete")

    elif job.disctype == "music":
        if utils.rip_music(job, logfile):
            utils.notify(job, "ARM notification", "Music CD: " + str(job.label) + " processing complete. ")
            utils.scan_emby(job)
            # This shouldnt be needed. but to be safe
            job.status = "success"
            db.session.commit()
        else:
            logging.info("Music rip failed.  See previous errors.  Exiting. ")
            job.eject()
            job.status = "fail"
            db.session.commit()

    elif job.disctype == "data":
        # get filesystem in order
        datapath = os.path.join(cfg["RAW_PATH"], str(job.label))
        if (utils.make_dir(datapath)) is False:
            ts = str(round(time.time() * 100))
            datapath = os.path.join(cfg["RAW_PATH"], str(job.label) + "_" + ts)

            if (utils.make_dir(datapath)) is False:
                logging.info("Could not create data directory: " + str(datapath) + ".  Exiting ARM. ")
                sys.exit()

        if utils.rip_data(job, datapath, logfile):
            utils.notify(job, "ARM notification", "Data disc: " + str(job.label) + " copying complete. ")
            job.eject()
        else:
            logging.info("Data rip failed.  See previous errors.  Exiting.")
            job.eject()

    else:
        logging.info("Couldn't identify the disc type. Exiting without any action.")


if __name__ == "__main__":
    # Make sure all directories are fully setup
    utils.arm_setup()
    log_path = PurePath(cfg['LOGPATH'], "NAS.log")
    log_file = Path(log_path)
    if log_file.is_file():
        logging.basicConfig(filename=log_file,
                            format='[%(asctime)s] %(levelname)s ARM: %(message)s',
                            datefmt=cfg['DATE_FORMAT'], level="DEBUG")
    else:
        logging.basicConfig(filename=cfg['INSTALLPATH'] + "NAS.log",
                            format='[%(asctime)s] %(levelname)s ARM: %(message)s',
                            datefmt=cfg['DATE_FORMAT'], level="DEBUG")
    args = entry()
    devpath = "/dev/" + args.devpath
    # print(devpath)
    job = Job(devpath)
    logfile = logger.setuplogging(job)
    if utils.get_cdrom_status(devpath) != 4:
        logging.info("Drive appears to be empty or is not ready.  Exiting ARM.")
        sys.exit()
    # Dont put out anything if we are using the empty.log
    # This kills multiple runs. it stops the same job triggering more than once
    if logfile.find("empty.log") != -1 or logfile.find("NAS_") != -1:
        sys.exit()

    logging.info("Starting ARM processing at " + str(datetime.datetime.now()))

    utils.check_db_version(cfg['INSTALLPATH'], cfg['DBFILE'])

    # put in db
    job.status = "active"
    job.start_time = datetime.datetime.now()
    db.session.add(job)
    db.session.commit()
    config = Config(cfg, job_id=job.job_id)
    db.session.add(config)
    db.session.commit()

    # Log version number
    with open(os.path.join(cfg["INSTALLPATH"], 'VERSION')) as version_file:
        version = version_file.read().strip()
    logging.info("ARM version: " + str(version))
    job.arm_version = version
    logging.info(("Python version: " + sys.version).replace('\n', ""))
    logging.info("User is: " + getpass.getuser())
    logger.cleanuplogs(cfg["LOGPATH"], cfg["LOGLIFE"])
    logging.info("Job: " + str(job.label))

    # a_jobs = Job.query.filter_by(status="active")
    a_jobs = db.session.query(Job).filter(Job.status.notin_(['fail', 'success'])).all()

    # Clean up abandoned jobs
    for j in a_jobs:
        if psutil.pid_exists(j.pid):
            p = psutil.Process(j.pid)
            if j.pid_hash == hash(p):
                logging.info("Job #" + str(j.job_id) + " with PID " + str(j.pid) + " is currently running.")
        else:
            logging.info("Job #" + str(j.job_id) + " with PID " + str(
                j.pid) + " has been abandoned.  Updating job status to fail.")
            j.status = "fail"
            db.session.commit()

    log_udev_params()

    try:
        main(logfile, job)
    except Exception as e:
        logging.exception("A fatal error has occurred and ARM is exiting.  See traceback below for details.")
        utils.notify(job, "ARM notification", "ARM encountered a fatal error processing " + str(
            job.title) + ". Check the logs for more details. " + str(e))
        job.status = "fail"
        job.eject()
    else:
        job.status = "success"
    finally:
        job.stop_time = datetime.datetime.now()
        joblength = job.stop_time - job.start_time
        minutes, seconds = divmod(joblength.seconds + joblength.days * 86400, 60)
        hours, minutes = divmod(minutes, 60)
        total_len = '{:d}:{:02d}:{:02d}'.format(hours, minutes, seconds)
        job.job_length = total_len
        db.session.commit()
