'''
Created on Jan 4, 2010

@author: nbryskin
'''

import logging
import logging.handlers
import threading
import inspect
import sys
import os.path
import os
import json
import datetime
import re
from contextlib import contextmanager

def add_arguments(parser):
    parser.add_argument('--logging-config', default='/etc/pyamod/logging.conf', help='path to logging config file')

def factory(*args, **kwargs):
    record = logging.LogRecord(*args, **kwargs)
    record.context = getattr(tl, 'context', '')
    return record

def init(options, appname):
    logging.config.dictConfig(json.load(open(options.logging_config)))
    glob.appname = appname
    logging.setLogRecordFactory(factory)

def getlogger(obj):
    if not hasattr(obj, '_logger'):
        name = inspect.getmodule(obj).__name__
        if name == '__main__':
            modfile = inspect.getmodule(obj).__file__
            name = inspect.getmodulename(modfile)
            for path in sys.path:
                if path != '' and modfile.startswith(path):
                    name = '.'.join(modfile[len(path)+1:].split(os.path.sep)[:-1]+[name])
                    break
        obj._logger = logging.getLogger('{0}.{1}'.format(glob.appname, name))
    return obj._logger

def setlogger(obj, logger):
    obj._logger = logger

def wrap(klass):
    if inspect.isfunction(klass):
        return wrapfunc(klass)

    klass.logger = property(fget=getlogger, fset=setlogger)
    for name, method in inspect.getmembers(klass, inspect.isfunction):
        if not hasattr(method, 'ignore'):
            setattr(klass, name, wrapfunc(method))
    return klass

def ignore(func):
    func.ignore = True
    return func

class Global(object):
    pass
glob = Global()
glob.enabled = True
glob.appname = ''
tl = threading.local()

def wrapgenerator(gen, logger, level, func):
    try:
        ident = '\t' * tl.identation
        tl.identation += 1
        for item in gen:
            logger.log(level, '{0}<~ {1}'.format(ident, func.__name__), extra={'params': item})
            yield item
    except Exception as e:
        logger.error(ident + '<- %s exception: %s', func.__name__, repr(e))
        raise
    finally:
        tl.identation -= 1

logging.TRACE = 5
logging._levelNames[logging.TRACE] = 'TRACE'
logging._levelNames['TRACE'] = logging.TRACE

def wrapfunc(func, level=logging.TRACE):
    def innerFunc(*args, **kwargs):
        if not hasattr(glob, 'enabled') or not glob.enabled:
            return func(*args, **kwargs)

        if not hasattr(tl, 'identation'):
            tl.identation = 0
        ident = '\t' * tl.identation

        if len(args) > 0 and hasattr(args[0], 'logger'):
            logger = args[0].logger
        else:
            logger = func.getlogger()

        logger.log(level, '{0} -> {1}'.format(ident, func.__name__), extra={'params': {'args': args, 'kwargs': kwargs}})
        if inspect.isgeneratorfunction(func):
            return wrapgenerator(func(*args, **kwargs), logger, level, func)
        else:
            tl.identation += 1
            try:
                result = func(*args, **kwargs)
            except BaseException as e:
                logger.error(ident + '<- %s exception: %s', func.__name__, repr(e))
                raise
            finally:
                tl.identation -= 1
            logger.log(level, ident + '<- %s %s', func.__name__, repr(result))
            return result
    func.getlogger = lambda: getlogger(func)
    if glob.enabled:
        result = innerFunc
        result.getlogger = func.getlogger
        result.__doc__ = func.__doc__
        result.__name__ = func.__name__
        result.__dict__.update(func.__dict__)
    else:
        result = func
    return result

def logged2(level=logging.TRACE):
    def wrap(func):
        return logged(func, level)
    return wrap

@contextmanager
def addcontext(context):
    oldcontext = getattr(tl, 'context', None)
    context = str(context)
    try:
        if oldcontext:
            tl.context = ' '.join([oldcontext, context])
        else:
            tl.context = context
        yield context
    finally:
        tl.context = oldcontext

def getcontext():
    return getattr(tl, 'context', '')

class ContextFilter(logging.Filter):
    def __init__(self, substring):
        self.substring = substring

    def filter(self, record):
        return self.substring in record.context

class RegexpFilter(logging.Filter):
    def __init__(self, field, pattern):
        self.field = field
        self.pattern = re.compile(pattern)

    def filter(self, record):
        return self.pattern.match(getattr(record, self.field)) is not None

class FileHandler(logging.FileHandler):
    def __init__(self, *args, terminator=logging.StreamHandler.terminator, errors=None, **kwargs):
        self.terminator = terminator
        self.errors = errors
        super().__init__(*args, **kwargs)

    def _open(self):
        return open(self.baseFilename, self.mode, encoding=self.encoding, errors=self.errors)

class WatchedFileHandler(logging.handlers.WatchedFileHandler):
    def __init__(self, *args, terminator=logging.StreamHandler.terminator, errors=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.terminator = terminator
        self.errors = errors

    def _open(self):
        return open(self.baseFilename, self.mode, encoding=self.encoding, errors=self.errors)

class MultiFileHandler(logging.Handler):
    def __init__(self, pattern, *args, **kwargs):
        super().__init__()
        self.pattern = pattern
        self.args = args
        self.kwargs = kwargs
        self.handlers = {}

    def setFormatter(self, formatter):
        super().setFormatter(formatter)
        for handler in self.handlers:
            handler.setFormatter(formatter)

    def emit(self, record):
        filename = self.pattern.format(now=datetime.datetime.now(), **record.__dict__)
        if not filename in self.handlers:
            os.makedirs(os.path.dirname(filename), exist_ok=True)
            handler = FileHandler(filename, *self.args, **self.kwargs)
            handler.setFormatter(self.formatter)
            self.handlers[filename] = handler
        return self.handlers[filename].emit(record)

    def flush(self):
        for handler in self.handlers:
            handler.flush()
        self.handlers = {}

class DispatchingFormatter:
    def __init__(self, formatters):
        self.formatters = [(re.compile(i['logger']), logging.Formatter(i['format'])) for i in formatters]

    def format(self, record):
        formatter = next(filter(lambda f: f[0].match(record.name), self.formatters))
        return formatter[1].format(record)
