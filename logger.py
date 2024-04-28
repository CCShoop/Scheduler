from datetime import datetime

def get_log_time():
    time = datetime.now().astimezone()
    output = ''
    if time.hour < 10:
        output += '0'
    output += f'{time.hour}:'
    if time.minute < 10:
        output += '0'
    output += f'{time.minute}:'
    if time.second < 10:
        output += '0'
    output += f'{time.second}'
    return output + '>'

def log(string: str):
    print(f'{get_log_time()} {string}')

def log_info(string: str):
    string = 'INFO> ' + string
    log(string)

def log_warn(string: str):
    string = 'WARN> ' + string
    log(string)

def log_error(string: str):
    string = 'ERROR> ' + string
    log(string)

def log_debug(string: str):
    string = 'DEBUG> ' + string
    log(string)
