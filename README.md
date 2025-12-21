# how to start
create new repository:
via UI then clone it; or using command
`git init`

install this package:
`pip install git+https://github.com/Sergey-Gureev/progect_gen.git`

use command:
`project_gen setup`
-> it will generate progect structure. Go to testproject.toml file and fill it 

use command:
`project_gen generate`

check `config/stg.yaml`;

go to tests and remove `@pytest.mark.skip`

known bugs to fix manually 
  in models/json_any.py add 
    "Dict[str, None]",  # or "Dict[str, Any]" Add this as a catch-all for complex dictionaries
