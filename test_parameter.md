{ "file_list": ["DSC_6920.jpeg","IMG_8212.jpeg","IMG_8166.jpeg","BOE_4891.jpeg","BOE_0591.jpeg"], "slideshow_index": 0}

{ "file_list": ["DSC_6920.jpeg","IMG_8212.jpeg","IMG_8166.jpeg","BOE_4891.jpeg","BOE_0591.jpeg"], "slideshow_index": 0}


curl -X POST http://localhost:5000/initialize_slideshow -H "Content-Type: application/json" -d '{ "keyword_list": ["Architektur"], "slideshow_index": 0}'


curl -X POST http://localhost:5000/show_slideshow_index -H "Content-Type: application/json" -d '{ "slideshow_index": 3}'

curl -X POST http://localhost:5000/sync_files -H "Content-Type: application/json" -d '{}'

curl -X POST http://localhost:5000/show_slideshow_next -H "Content-Type: application/json" -d '{}'