from glob import glob

from setuptools import find_packages, setup

package_name = 'cleannav_mission_manager'

setup(
    name=package_name,
    version='0.0.0',
    packages=find_packages(exclude=['test']),
    data_files=[
        ('share/ament_index/resource_index/packages',
            ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml']),
        ('share/' + package_name + '/launch', glob('launch/*.launch.py')),
    ],
    install_requires=['setuptools'],
    zip_safe=True,
    maintainer='CleanNav Team',
    maintainer_email='1339980053@qq.com',
    description='CleanNav Mission Manager M1 mock implementation',
    license='Apache-2.0',
    extras_require={
        'test': [
            'pytest',
        ],
    },
    entry_points={
        'console_scripts': [
            'mission_manager_node = cleannav_mission_manager.node:main',
        ],
    },
)
