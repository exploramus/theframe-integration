#!/usr/bin/with-contenv bashio
# shellcheck shell=bash
set -e

bashio::log.info "Starting The Frame Addon ..."

# Load configuration
TOKEN_FILE=$(bashio::config 'token_file')
bashio::log.info "Token file: ${TOKEN_FILE}"
TV_IP=$(bashio::config 'tv_ip')
bashio::log.info "TV IP: ${TV_IP}"
TV_MAC=$(bashio::config 'tv_mac')
bashio::log.info "TV MAC: ${TV_MAC}"
JSON_PATH=$(bashio::config 'json_path')
bashio::log.info "JSON Path: ${JSON_PATH}"
IMAGE_PATH=$(bashio::config 'image_path')
bashio::log.info "Image Path: ${IMAGE_PATH}"
MATTE=$(bashio::config 'matte')
bashio::log.info "Matte: ${MATTE}"

exec fastapi run theframe.py --port 5000
bashio::log.info "The Frame Addon started successfully."
