"""Launch the PC-only real-adapter Mission Manager composition."""

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def generate_launch_description() -> LaunchDescription:
    """Create the PC Demo entry point without changing Navigation config."""
    return LaunchDescription([
        DeclareLaunchArgument(
            'use_sim_time',
            default_value='true',
            description='Use the Gazebo simulation clock when true.',
        ),
        Node(
            package='cleannav_mission_manager',
            executable='cleannav_pc_demo',
            name='mission_manager_pc_demo',
            output='screen',
            parameters=[{
                'use_sim_time': LaunchConfiguration('use_sim_time'),
            }],
        ),
    ])
