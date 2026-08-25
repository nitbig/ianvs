from simulators import (
    EnvironmentAdministrator,
    JobAdministrator,
    ConfigurationError,
    EnvironmentError
)

# Add this method to TestCaseController class
def run_simulation_test(self, config_path: str) -> Dict[str, Any]:
    """Run simulation-based test"""
    try:
        # Load environment config
        env_admin = EnvironmentAdministrator(config_path)
        env_admin.load_config()
        
        # Build environment
        env_id = env_admin.build_environment()
        logger.info(f'Simulation environment: {env_id}')
        
        # Deploy modules
        modules = ['database', 'message_queue', 'monitoring']
        env_admin.deploy_modules(modules)
        logger.info(f'Modules deployed')
        
        # Create job admin
        job_admin = JobAdministrator(env_id, env_admin.env_dir)
        
        # Deploy test job
        job_config = {
            'name': 'test_job',
            'algorithm': 'test_algorithm',
            'workers': 2,
            'dataset': '/data/test',
            'epochs': 5,
            'batch_size': 32
        }
        
        yaml_path = os.path.join(env_admin.env_dir, 'test_job.yaml')
        job_admin.generate_job_yaml(job_config, yaml_path)
        
        job_id = job_admin.deploy(yaml_path, num_workers=2)
        logger.info(f'Job deployed: {job_id}')
        
        # Wait for completion
        job_admin.watch(job_id)
        
        # Get results
        results = job_admin.get_results(job_id)
        
        # Cleanup
        job_admin.delete(job_id)
        env_admin.cleanup_environment()
        
        return {
            'status': 'success',
            'environment_id': env_id,
            'job_id': job_id,
            'results': results
        }
    
    except (ConfigurationError, EnvironmentError) as e:
        logger.error(f'Simulation test failed: {e}')
        raise