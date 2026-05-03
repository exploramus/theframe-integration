Playlists

# Ameland
file_names: ["DSC_3218.jpeg", "DSC_3288 (1).jpeg", "DSC_3301.jpeg", "IMG_2809.jpeg", "IMG_2849.jpeg", "IMG_2877.jpeg"]


curl -X POST http://homeassistant.local:5000/initialize_slideshow_files \
     -H "Content-Type: application/json" \
     -d '{"file_names":["DSC_3218.jpeg", "DSC_3288 (1).jpeg", "DSC_3301.jpeg", "IMG_2809.jpeg", "IMG_2849.jpeg", "IMG_2877.jpeg"],"slideshow_index":0}'