"""Launch the mock-only Mission Manager ROS glue runtime."""

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def generate_launch_description() -> LaunchDescription:
    """Create the explicit mock integration launch description."""
    return LaunchDescription([
        DeclareLaunchArgument(
            'use_sim_time',
            default_value='false',
            description='Use the ROS simulation clock when true.',
        ),
        Node(
            package='cleannav_mission_manager',
            executable='mission_manager_node',
            name='mission_manager_node',
            output='screen',
            parameters=[{
                'runtime_mode': 'mock',
                'use_sim_time': LaunchConfiguration('use_sim_time'),
            }],
        ),
    ])
