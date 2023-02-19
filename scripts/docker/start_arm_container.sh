#!/bin/bash

function add_drives(){
    for dev in /dev/sr?; do
            echo "--device=\"${dev}:${dev}\" \\"
    done
}

ARMID=$(id -u arm)
ARMGID=$(id -g arm)

echo 'docker run -d \
    -p "8080:8080" \
    -e ARM_UID="'$ARMID'" \
    -e ARM_GID="'$ARMGID'" \
    -v "<path_to_arm_user_home_folder>:/home/arm" \
    -v "<path_to_music_folder>:/home/arm/Music" \
    -v "<path_to_logs_folder>:/home/arm/logs" \
    -v "<path_to_media_folder>:/home/arm/media" \
    -v "<path_to_config_folder>:/etc/arm/config" \
    '$(add_drives)'
    --privileged \
    --restart "always" \
    --name "arm-rippers" \
    automaticrippingmachine/automatic-ripping-machine:latest'
