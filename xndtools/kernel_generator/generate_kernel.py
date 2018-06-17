""" Provides: generate_kernel, get_module_data.
"""
# Author: Pearu Peterson
# Created: April 2018

import os
import sys
from glob import glob
from copy import deepcopy
from .readers import PrototypeReader, load_kernel_config
from .utils import NormalizedTypeMap, split_expression
from .kernel_source_template import source_template

def apply_typemap(prototype, typemap, typemap_tests):
    orig_type = prototype['type']
    prototype['type'] = normal_type = typemap(orig_type)
    prototype['ctype'] = c_type = normal_type + '_t'
    if orig_type != normal_type:
        typemap_tests.add((orig_type, c_type))
    
    for arg in prototype['arguments']:
        orig_type = arg['type']
        arg['type'] = normal_type = typemap(orig_type)
        arg['ctype'] = c_type = normal_type + '_t'
        if orig_type != normal_type:
            typemap_tests.add((orig_type, c_type))
    
def generate_kernel(config_file,
                    target_file = None,
                    source_dir = ''):
    data = get_module_data(config_file)
    source = source_template(data)
    own_target_file = False
    if target_file == 'stdout':
        target_file = sys.stdout
        own_target_file = False
    else:
        if target_file is None:
            target_file = os.path.join(source_dir, '{module_name}-kernels.c'.format(**data))
        if isinstance(target_file, str):
            print('generate_kernel: kernel sources are saved to {}'.format(target_file))
            target_file = open(target_file, 'w')
            own_target_file = True
        else:
            own_target_file = False

    target_file.write(source['c_source'])
    if own_target_file:
        target_file.close()
    return dict(config_file = config_file,
                sources = [target_file.name] + data['sources'])

def get_module_data(config_file, package=None):
    config = load_kernel_config(config_file)
    reader = PrototypeReader()    
    current_module = None
    xndtools_datadir = os.path.dirname(__file__)
    include_dirs = [xndtools_datadir]
    sources = list(glob(os.path.join(xndtools_datadir, '*.c')))
    kernels = []
    typemap_tests = set()
    
    default_kinds_value = 'Xnd' # TODO: move to command line options
    default_ellipses_value = '...'
    default_arraytypes_value = 'symbolic'
    
    for section in config.sections():
        if section.startswith('MODULE'):
            assert current_module is None
            module_name = section.split(None, 1)[1]
            current_module = config[section]

            typemap = NormalizedTypeMap()
            for line in current_module.get('typemaps', '').splitlines():
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                left, right = line.split(':', 1)
                typemap[left.strip()] = right.strip()

            for line in current_module.get('include_dirs', '').splitlines():
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                include_dirs.append(line)

            for line in current_module.get('sources', '').splitlines():
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                sources.append(line)

            default_kinds = split_expression(current_module.get('kinds', default_kinds_value))
            default_ellipses = split_expression(current_module.get('ellipses', default_ellipses_value))
            default_arraytypes = split_expression(current_module.get('arraytypes', default_arraytypes_value))
            
        elif section.startswith('KERNEL'):
            f = config[section]
            if f.get('skip', None):
                #print ('skipping', section)
                continue
            kernel_name = section.split(maxsplit=1)[1].strip()
            description = f.get('description','').strip()

            prototypes = reader(f.get('prototypes',''))
            prototypes_C = reader(f.get('prototypes_C', ''))
            prototypes_Fortran = reader(f.get('prototypes_Fortran',''))

            if not (prototypes or prototypes_C or prototypes_Fortran):
                print('get_module_data: no prototypes|prototypes_C|prototypes_Fortran defined in [KERNEL {}]'.format(kernel_name))
                continue

            debug = bool(f.get('debug', False))
            kinds = split_expression(f.get('kinds', ''))
            ellipses = f.get('ellipses')
            if ellipses is None:
                ellipses = default_ellipses
            else:
                ellipses = split_expression(ellipses)
            arraytypes = split_expression(f.get('arraytypes', '')) or default_arraytypes

            assert set(arraytypes).issubset(['symbolic', 'variable']),repr(arraytypes)
            
            # set argument intents
            input_arguments = split_expression(f.get('input_arguments', ''))
            output_arguments = split_expression(f.get('output_arguments', ''))
            inplace_arguments = split_expression(f.get('inplace_arguments', ''))
            hide_arguments = split_expression(f.get('hide_arguments', ''))
                    
            # resolve argument shapes
            shape_map = {}
            
            for name_shape in split_expression(f.get('dimension', '')):
                i = name_shape.index('(')
                if i==-1 or name_shape[-1] !=')':
                    print('cannot determine shape from {!r}. IGNORING.'.format(name_shape))
                    continue
                name = name_shape[:i].strip()
                shape = [a.strip() for a in name_shape[i+1:-1].split(',')]
                shape_map[name] = shape

            # propagate prototypes to kernels
            for prototypes_, kinds_ in [
                    (prototypes_C, ['C']),
                    (prototypes_Fortran, ['Fortran']),
                    (prototypes, kinds or default_kinds),
            ]:
                for prototype in prototypes_:
                    prototype['kernel_name'] = kernel_name
                    prototype['description'] = description
                    prototype['function_name'] = prototype.pop('name')
                    prototype['debug'] = debug
                    apply_typemap(prototype, typemap, typemap_tests)

                    for name in input_arguments:
                        prototype.set_argument_intent(name, 'input')
                    for name in inplace_arguments:
                        prototype.set_argument_intent(name, 'inplace')
                    for name in output_arguments:
                        prototype.set_argument_intent(name, 'output')
                    for name in hide_arguments:
                        prototype.set_argument_intent(name, 'hide')
                    for name, shape in shape_map.items():
                        prototype.set_argument_shape(name, shape)

                    for arraytype in arraytypes:
                        for kind in kinds_:
                            if arraytype == 'variable' and kind != 'Xnd':
                                continue
                            for ellipses_ in ellipses:
                                kernel = deepcopy(prototype)
                                kernel['kind'] = kind
                                kernel['arraytype'] = arraytype
                                if ellipses_ and ellipses_.lower() != 'none':
                                    if ellipses_ == '...' and arraytype == 'variable':
                                        kernel['ellipses'] = 'var' + ellipses_ + ' * '
                                    else:
                                        kernel['ellipses'] = ellipses_ + ' * '
                                else:
                                    kernel['ellipses'] = ''

                                kernels.append(kernel)
                                #print('  {kernel_name}: using {function_name} for {kind}, ellipses={ellipses!r}'.format_map(kernel))

    l = []
    for h in current_module.get('includes','').split():
        h = h.strip()
        if h:
            l.append('#include "{}"'.format(h))
            
    module_data = dict(
        module_name = module_name,
        includes = '\n'.join(l),
        #header_code = current_module.get('header_code', ''),
        include_dirs = include_dirs,
        sources = sources,
        kernels = kernels,
        typemap_tests = list([dict(orig_type=o[0], normal_type=o[1]) for o in typemap_tests]),
    )

    return module_data
